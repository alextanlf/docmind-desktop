"""量 OCR 渲染 DPI 对识别质量与速度的影响。

**为什么必须量。** DPI 当初是拍的，而这个项目已经两次被实测推翻直觉（相似度分布几乎
完全重叠、小语料上 BM25 的 IDF 退化）。本脚本量的是**产出量**（抽出了多少字符）与耗时。

⚠️ **产出量不等于准确率。** 实测两本书上 150/200/300 的字符数比值都是 0.994–0.996，
看起来「完全等价」；但字符级对比显示三档是在**犯不同的错**（300 认对下标 `A3`，150 认成
`Ag`；150 在公式区多吐垃圾）。要判断哪档更准，用
`scripts/measure_ocr_accuracy.py`（以文字层为参考量字符错误率），不要用这支脚本下结论。

DPI 同时影响速度和召回：

* 太低 → 小字号正文、上下标、公式丢字，而扫描件正是靠这些字被检索到。
* 太高 → 渲染变慢、临时文件变大，而识别对超过约 300 dpi 的输入通常不再有增益。

所以这里量的是「每页抽出多少字符」「花了多久」，以及**逐页配对的差异**——只看总数会
被页间长度差异掩盖。

用法::

    uv run python scripts/measure_ocr_dpi.py 扫描书.pdf --pages 40
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

#: 待比较的 DPI 档位。150 是可疑的下限，300 是「再高也没用」的上界。
DPI_LEVELS = (150, 200, 300)

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def _needs_ocr(page: pymupdf.Page) -> bool:
    from app.document.page_kind import classify_page

    return classify_page(page).page_ocr


@dataclass
class PageMeasurement:
    page: int
    dpi: int
    chars: int
    cjk: int
    latin: int
    lines: int
    render_seconds: float
    recognize_seconds: float
    #: 像素尺寸，用来解释「为什么这档更慢」。
    pixels: int


def _counts(text: str) -> tuple[int, int, int]:
    return len(text), len(_CJK.findall(text)), len(_LATIN.findall(text))


def measure(path: Path, pages: int, levels: tuple[int, ...]) -> dict[str, object]:
    document = pymupdf.open(path)
    # 等间隔取页，避免只看到某一章（同一本书不同章节的排版密度差异很大）。
    total = document.page_count
    indices = [int(index * total / pages) for index in range(min(pages, total))]
    # 只测需要 OCR 的页：原生文字页根本不走这条路，测它们没有意义。
    selected = [index for index in indices if _needs_ocr(document[index])]

    measurements: list[PageMeasurement] = []
    texts: dict[tuple[int, int], str] = {}
    for index in selected:
        page = document[index]
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
            text = recognized[0].text() if recognized else ""
            chars, cjk, latin = _counts(text)
            texts[(index, dpi)] = text
            measurements.append(
                PageMeasurement(
                    page=index + 1,
                    dpi=dpi,
                    chars=chars,
                    cjk=cjk,
                    latin=latin,
                    lines=len(recognized[0].lines) if recognized else 0,
                    render_seconds=round(render_seconds, 3),
                    recognize_seconds=round(recognize_seconds, 3),
                    pixels=pixmap.width * pixmap.height,
                )
            )
    document.close()

    summary: dict[str, object] = {}
    for dpi in levels:
        rows = [row for row in measurements if row.dpi == dpi]
        if not rows:
            continue
        summary[str(dpi)] = {
            "pages": len(rows),
            "mean_chars": round(sum(row.chars for row in rows) / len(rows), 1),
            "mean_cjk": round(sum(row.cjk for row in rows) / len(rows), 1),
            "mean_lines": round(sum(row.lines for row in rows) / len(rows), 1),
            "mean_render_seconds": round(
                sum(row.render_seconds for row in rows) / len(rows), 3
            ),
            "mean_recognize_seconds": round(
                sum(row.recognize_seconds for row in rows) / len(rows), 3
            ),
            "mean_total_seconds": round(
                sum(row.render_seconds + row.recognize_seconds for row in rows) / len(rows),
                3,
            ),
            "mean_pixels": int(sum(row.pixels for row in rows) / len(rows)),
        }

    # 逐页配对比较：只看均值会被「某些页本来就长」掩盖。
    baseline = levels[0] if levels else 200
    paired: dict[str, object] = {}
    for dpi in levels:
        if dpi == baseline:
            continue
        deltas = [
            len(texts[(page, dpi)]) - len(texts[(page, baseline)]) for page in selected
        ]
        ratios = [
            len(texts[(page, dpi)]) / max(len(texts[(page, baseline)]), 1) for page in selected
        ]
        paired[f"{dpi}_vs_{baseline}"] = {
            "mean_char_delta": round(sum(deltas) / max(len(deltas), 1), 1),
            "mean_char_ratio": round(sum(ratios) / max(len(ratios), 1), 3),
            "pages_shorter": sum(1 for delta in deltas if delta < 0),
            "pages_longer": sum(1 for delta in deltas if delta > 0),
            "pages_equal": sum(1 for delta in deltas if delta == 0),
        }

    return {
        "path": path.name,
        "pages_measured": len(selected),
        "summary": summary,
        "paired": paired,
        "pages": [asdict(row) for row in measurements],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--pages", type=int, default=20)
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
    else:
        print(json.dumps({k: v for k, v in payload.items() if k != "pages"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
