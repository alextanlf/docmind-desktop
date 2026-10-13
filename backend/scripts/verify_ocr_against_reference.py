"""用**独立来源的文字版**核对 OCR 准确率。

**为什么不能拿 PDF 的文字层当参考答案。** 那本身就是另一次 OCR 的产物，只能回答「谁的
输出更像另一个 OCR」，不能回答「谁更接近真实文本」。实测两次都因此得出反向结论：

* 拿 Feynman 的文字层当参考，会得出「Vision 不比它好」——而那份文字层 `Thad only come
  to watch`（I had only…）、`What I had intended to do w`（截断）、结尾整段被拆散重排，
  是真正的损坏。
* 拿 ClearScan 的文字层当参考，会把 `r:emoves`、`I 0241` 这类伪影当成正确答案。

所以参照物必须**独立于被评估的 PDF**：另一条数字化流水线、人工校对过的文本，或任何不是
从这个文件里抽出来的东西。

**对齐方式。** 扫描 PDF 的页边界与文字版的行边界对不上，逐行比对会得到一堆假错误。这里
按「页」评估：把参考答案按等长切片喂给每页比对，用滑动窗口在参考文本里找该页最匹配的位置
再算编辑距离。找不到足够像的位置时该页**记为未匹配**，不计入统计 —— 宁可少报也不要报错。

用法::

    uv run python scripts/verify_ocr_against_reference.py 扫描书.pdf 权威文字.txt --pages 20
    uv run python scripts/verify_ocr_against_reference.py 扫描书.pdf 权威文字.txt --page-range 70:80
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document import ocr
from app.document.page_kind import classify_page

DPI_LEVELS = (150, 200, 300)
_WHITESPACE = re.compile(r"\s+")
#: 参考文本里的词。用它做滑动窗口匹配，比逐字符快得多。
_WORD = re.compile(r"[A-Za-z]{2,}")
#: 一页至少要有这么多字符才参与评估（页眉、章首页没有信息量）。
MIN_PAGE_CHARS = 200
#: 窗口相似度低于此值就认为「参考文本里没有这一页」，记为未匹配。
MIN_ALIGNMENT = 0.5


def normalize(text: str) -> str:
    """去掉全部空白并统一引号。

    换行与空格是排版差异；引号与破折号在不同数字化流水线里编码不同，不统一会把它们
    算成识别错误。
    """
    text = _WHITESPACE.sub("", text.lower())
    for fancy, plain in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), ("—", "-"), ("–", "-")):
        text = text.replace(fancy, plain)
    return text


def edit_distance(reference: str, hypothesis: str) -> int:
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, ref_char in enumerate(reference, start=1):
        current = [row]
        for column, hyp_char in enumerate(hypothesis, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (ref_char != hyp_char),
                )
            )
        previous = current
    return previous[-1]


#: 锚点串长度。太短会在参考文本里撞到多处，太长则可能因为识别错误而找不到。
_ANCHOR_LENGTH = 24
#: 锚点串允许的编辑距离（按锚点长度比例）。OCR 总有几个字符错，完全相等会一个锚点都
#: 找不到；放太宽又会锚错位置。
_ANCHOR_TOLERANCE = 0.15


def _anchor_candidates(page_text: str) -> list[str]:
    """从页面文本里挑几段适合做定位锚的串。

    取最长单词附近的长片段：它们最不容易在参考文本里重复出现，也最不容易被 OCR 改得
    面目全非（相比数字与小词）。
    """
    clean = _WHITESPACE.sub(" ", page_text)
    candidates: list[str] = []
    pattern = rf"[A-Za-z0-9 ,.;:'\-]{{{_ANCHOR_LENGTH},}}"
    for match in re.finditer(pattern, clean):
        fragment = match.group(0).strip()
        if len(fragment) >= _ANCHOR_LENGTH:
            candidates.append(fragment[:_ANCHOR_LENGTH * 2])
    # 均匀取几个，避免全挤在同一段。
    if len(candidates) > 5:
        stride = len(candidates) // 5
        candidates = candidates[::stride][:5]
    return candidates


def _find_anchor(reference: str, anchor: str) -> int:
    """在参考文本里找锚点串的位置，返回 -1 表示找不到。

    先用精确查找（快），失败再退化成分块容错匹配：把锚点切成 4 段，允许其中一段有错，
    在参考文本里找同时命中其余三段的窗口。扫描件的 OCR 几乎必然改掉几个字符，只靠精确
    查找会大面积锚不上。
    """
    exact = reference.find(anchor)
    if exact >= 0:
        return exact
    piece = max(len(anchor) // 4, 4)
    parts = [anchor[index : index + piece] for index in range(0, len(anchor) - piece + 1, piece)]
    if len(parts) < 3:
        return -1
    # 用中间几段去找候选位置：首尾段最容易因为边界效应失配。
    middle = parts[len(parts) // 2]
    position = reference.find(middle)
    if position < 0:
        return -1
    window_start = max(0, position - len(anchor))
    window_end = min(len(reference), position + len(anchor) * 2)
    window = reference[window_start:window_end]
    best_offset, best_distance = -1, len(anchor)
    step = max(piece // 2, 1)
    for offset in range(0, max(len(window) - len(anchor), 1), step):
        candidate = window[offset : offset + len(anchor)]
        distance = edit_distance(anchor, candidate)
        if distance < best_distance:
            best_offset, best_distance = offset, distance
    if best_offset < 0 or best_distance > len(anchor) * _ANCHOR_TOLERANCE:
        return -1
    return window_start + best_offset


def align_page(reference: str, page_text: str) -> tuple[int, float]:
    """把这一页对到参考文本上，返回 ``(参考中的起点, 相似度)``。

    锚定而不是滑窗：参考文本动辄上百万字符，逐位置滑动在时间上不可行。锚点的作用是拿到
    一个**大致**位置，再据此截出与页面等长的片段算编辑距离 —— 相似度由这一步给出，锚点
    本身不参与评分，所以锚错一点点不会污染结论。
    """
    for anchor in _anchor_candidates(page_text):
        position = _find_anchor(reference, anchor)
        if position < 0:
            continue
        # 页面文本去掉空白后的长度就是它在参考里的长度；向前留一点余量再截。
        size = max(len(page_text), MIN_PAGE_CHARS)
        start = max(0, position - size // 8)
        window = reference[start : start + size]
        score = 1.0 - edit_distance(window, page_text) / max(len(window), 1)
        if score >= MIN_ALIGNMENT:
            return start, round(score, 4)
    return -1, 0.0


@dataclass
class PageCheck:
    page: int
    dpi: int
    reference_chars: int
    recognized_chars: int
    alignment: float
    cer: float
    render_seconds: float
    recognize_seconds: float


def verify(
    path: Path,
    reference: str,
    pages: int,
    dpi_levels: tuple[int, ...],
    page_range: tuple[int, int] | None,
) -> dict[str, object]:
    document = pymupdf.open(path)
    if page_range is not None:
        indices = list(range(page_range[0] - 1, min(page_range[1], document.page_count)))
    else:
        total = document.page_count
        indices = [int(index * total / pages) for index in range(min(pages, total))]

    reference_normalized = normalize(reference)
    rows: list[PageCheck] = []
    unmatched: list[int] = []
    for index in indices:
        page = document[index]
        if not classify_page(page).page_ocr:
            # 只评估走 OCR 的页：原生文字页根本不经过识别，评估它们没有意义。
            continue
        layer = page.get_text("text").strip()
        if len(normalize(layer)) < MIN_PAGE_CHARS:
            continue
        for dpi in dpi_levels:
            started = time.perf_counter()
            pixmap = page.get_pixmap(dpi=dpi)
            pixels = pixmap.tobytes("png")
            render_seconds = time.perf_counter() - started
            image = ocr.RenderedImage(
                pixels=pixels,
                width=float(pixmap.width),
                height=float(pixmap.height),
                page_rect=(page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1),
                page_number=index + 1,
            )
            started = time.perf_counter()
            recognized = ocr.recognize([image])
            recognize_seconds = time.perf_counter() - started
            hypothesis = normalize(recognized[0].text() if recognized else "")
            start, alignment = align_page(reference_normalized, hypothesis)
            if start < 0 or alignment < MIN_ALIGNMENT:
                if dpi == dpi_levels[0]:
                    unmatched.append(index + 1)
                continue
            window = reference_normalized[start : start + max(len(hypothesis), MIN_PAGE_CHARS)]
            rows.append(
                PageCheck(
                    page=index + 1,
                    dpi=dpi,
                    reference_chars=len(window),
                    recognized_chars=len(hypothesis),
                    alignment=alignment,
                    cer=round(edit_distance(window, hypothesis) / max(len(window), 1), 4),
                    render_seconds=round(render_seconds, 3),
                    recognize_seconds=round(recognize_seconds, 3),
                )
            )
    document.close()

    summary: dict[str, object] = {}
    for dpi in dpi_levels:
        selected = [row for row in rows if row.dpi == dpi]
        if not selected:
            continue
        cers = sorted(row.cer for row in selected)
        summary[str(dpi)] = {
            "pages": len(selected),
            "mean_cer": round(sum(cers) / len(cers), 4),
            "median_cer": round(cers[len(cers) // 2], 4),
            "mean_alignment": round(sum(row.alignment for row in selected) / len(selected), 4),
            "mean_render_seconds": round(
                sum(row.render_seconds for row in selected) / len(selected), 3
            ),
            "mean_recognize_seconds": round(
                sum(row.recognize_seconds for row in selected) / len(selected), 3
            ),
        }

    return {
        "path": path.name,
        "reference_chars": len(reference_normalized),
        "pages_matched": len({row.page for row in rows}),
        "pages_unmatched": unmatched,
        "summary": summary,
        "pages": [asdict(row) for row in rows],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("reference", type=Path, help="独立来源的文字版（.txt / .md / .html）")
    parser.add_argument("--pages", type=int, default=16)
    parser.add_argument("--page-range", type=str, default=None, help="如 70:80（含首含尾）")
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()

    if ocr.find_backend() is None:
        print(f"没有可用的识别后端：{ocr.unavailable_reason()}", file=sys.stderr)
        return 2

    raw = arguments.reference.read_text(encoding="utf-8", errors="replace")
    if arguments.reference.suffix.lower() in {".html", ".htm"}:
        # 粗略去标签：这一步不追求精确，标签残留只会略微降低对齐分数。
        raw = re.sub(r"<[^>]+>", " ", raw)
    page_range = None
    if arguments.page_range:
        first, _, last = arguments.page_range.partition(":")
        page_range = (int(first), int(last))

    payload = verify(arguments.pdf, raw, arguments.pages, DPI_LEVELS, page_range)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
        print(f"已写入 {arguments.out}")
    print(json.dumps({k: v for k, v in payload.items() if k != "pages"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
