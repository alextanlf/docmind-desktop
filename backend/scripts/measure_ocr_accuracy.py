"""量 OCR 在不同渲染 DPI 下的**字符错误率**。

**为什么要另写一支脚本。** `measure_ocr_dpi.py` 只比「抽出了多少字符」，而两本书的结果是
150/200/300 三档字符数几乎相同（比值 0.994–0.996）。字符数相同不等于内容相同：字符级
对比显示三档是**在犯不同的错**（300 认对下标 ``A3``，150 认成 ``Ag``；150 在公式区多吐
垃圾）。只看产出量无法判断哪档更准，必须有参考答案。

**参考答案从哪来。** 用「扫描 + 已有 OCR 文字层」的书：它的文字层是别人做的 OCR 结果，
不完美，但它是**独立于本次识别**的参考，能给出「不同 DPI 之间谁更接近真实文本」的相对
结论。这不等于绝对准确率 —— 报告里必须把这条边界说清楚。

评的是 **CER（字符错误率）= 编辑距离 / 参考长度**，按去掉空白后的字符序列计算：换行与
空格是排版差异，不是识别错误。

用法::

    uv run python scripts/measure_ocr_accuracy.py 算法导论.pdf --pages 30
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

DPI_LEVELS = (150, 200, 300)
#: 参考文本少于这么多字符的页跳过：页眉、章首页、插图页的文字层只有页码或水印，
#: 拿它们算错误率会得到「错误率 0%」这种毫无信息量的数。
MIN_REFERENCE_CHARS = 200

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """去掉全部空白。

    换行与空格是排版差异（PDF 的文字层会把一行拆成多个 block），不是识别错误。
    不归一化会让 CER 主要反映「断行位置不同」而不是「认错了字」。
    """
    return _WHITESPACE.sub("", text)


def edit_distance(reference: str, hypothesis: str) -> int:
    """Levenshtein 距离，滚动数组实现。

    参考与假设都可能上千字符，需要 O(n*m) 时间、O(min(n,m)) 空间。
    """
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


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference = normalize(reference)
    hypothesis = normalize(hypothesis)
    if not reference:
        return 0.0
    return edit_distance(reference, hypothesis) / len(reference)


@dataclass
class PageAccuracy:
    page: int
    dpi: int
    reference_chars: int
    recognized_chars: int
    edits: int
    cer: float
    render_seconds: float
    recognize_seconds: float


def measure(path: Path, pages: int, levels: tuple[int, ...]) -> dict[str, object]:
    document = pymupdf.open(path)
    total = document.page_count
    indices = [int(index * total / pages) for index in range(min(pages, total))]

    rows: list[PageAccuracy] = []
    for index in indices:
        page = document[index]
        reference = page.get_text("text").strip()
        if len(normalize(reference)) < MIN_REFERENCE_CHARS:
            continue
        for dpi in levels:
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
            hypothesis = recognized[0].text() if recognized else ""
            rows.append(
                PageAccuracy(
                    page=index + 1,
                    dpi=dpi,
                    reference_chars=len(normalize(reference)),
                    recognized_chars=len(normalize(hypothesis)),
                    edits=edit_distance(normalize(reference), normalize(hypothesis)),
                    cer=round(character_error_rate(reference, hypothesis), 4),
                    render_seconds=round(render_seconds, 3),
                    recognize_seconds=round(recognize_seconds, 3),
                )
            )
    document.close()

    summary: dict[str, object] = {}
    for dpi in levels:
        selected = [row for row in rows if row.dpi == dpi]
        if not selected:
            continue
        summary[str(dpi)] = {
            "pages": len(selected),
            "mean_cer": round(sum(row.cer for row in selected) / len(selected), 4),
            "median_cer": round(sorted(row.cer for row in selected)[len(selected) // 2], 4),
            "mean_reference_chars": round(
                sum(row.reference_chars for row in selected) / len(selected), 1
            ),
            "mean_render_seconds": round(
                sum(row.render_seconds for row in selected) / len(selected), 3
            ),
            "mean_recognize_seconds": round(
                sum(row.recognize_seconds for row in selected) / len(selected), 3
            ),
        }

    # 逐页配对：同一页在不同 DPI 下谁更接近参考答案。
    paired: dict[str, object] = {}
    by_page: dict[int, dict[int, PageAccuracy]] = {}
    for row in rows:
        by_page.setdefault(row.page, {})[row.dpi] = row
    baseline = 150 if 150 in levels else levels[0]
    for dpi in levels:
        if dpi == baseline:
            continue
        wins = losses = ties = 0
        deltas: list[float] = []
        for page_rows in by_page.values():
            if dpi not in page_rows or baseline not in page_rows:
                continue
            delta = page_rows[dpi].cer - page_rows[baseline].cer
            deltas.append(delta)
            if delta < -0.005:
                wins += 1
            elif delta > 0.005:
                losses += 1
            else:
                ties += 1
        paired[f"{dpi}_vs_{baseline}"] = {
            "pages": len(deltas),
            "mean_cer_delta": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
            "better_than_baseline": wins,
            "worse_than_baseline": losses,
            "indistinguishable": ties,
        }

    return {
        "path": path.name,
        "pages_measured": len(by_page),
        "summary": summary,
        "paired": paired,
        "pages": [asdict(row) for row in rows],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--pages", type=int, default=24)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()

    if ocr.find_backend() is None:
        print(f"没有可用的识别后端：{ocr.unavailable_reason()}", file=sys.stderr)
        return 2

    payload = measure(arguments.path, arguments.pages, DPI_LEVELS)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if arguments.out is not None:
        arguments.out.write_text(text, encoding="utf-8")
        print(f"已写入 {arguments.out}")
    print(json.dumps({k: v for k, v in payload.items() if k != "pages"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
