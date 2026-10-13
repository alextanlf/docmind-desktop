"""量导入一份扫描件时的**峰值常驻内存**，以及它花在哪一段。

**为什么必须量。** 「常驻 425 MB」是之前按批大小推算出来的（2→214MB、4→311MB、
8→419MB、16→684MB、32→1159MB），但那是**子进程批量识别**那一侧的数字。整条导入链路里
还有一处按页数线性增长的东西：`_pdf_sections` 会把每一页的图**全部渲染成 PNG 字节**留在
内存里，等整份文档遍历完才开始识别。一本 400 页的扫描书按 150 dpi 是几百 MB，而它在识别
开始之前就已经占住了。

所以这支脚本量的是**进程峰值 RSS**（`ru_maxrss`），并把内存拆成两段：

* **渲染段**：遍历所有页、产出全部 PNG 字节。
* **识别段**：把这些字节分批交给识别子进程。

两段的峰值分开报，才能知道该改哪一段 —— 只报一个总数无法判断是"渲染留太多"还是
"识别子进程太胖"。

用法::

    uv run python scripts/measure_import_memory.py 扫描书.pdf
    uv run python scripts/measure_import_memory.py 扫描书.pdf --pages 120
"""
from __future__ import annotations

import argparse
import gc
import json
import resource
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.document import ocr, page_kind
from app.document.builtin_formats import _pdf_sections

#: macOS 的 `ru_maxrss` 单位是字节，Linux 是 KB。这里按平台换算成字节。
_BYTES_PER_UNIT = 1 if sys.platform == "darwin" else 1024


def peak_rss_bytes() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * _BYTES_PER_UNIT


@dataclass
class Stage:
    """一段的峰值与耗时。"""

    name: str
    peak_seconds: float
    peak_rss_mb: float
    pages: int
    rendered_bytes_mb: float


def _render_all(path: Path, pages: int | None) -> tuple[list[tuple[int, ocr.RenderedImage, list]], int]:
    """渲染段：把每一页的图渲染出来留在内存里，返回它们与总字节数。

    这里刻意复用**生产同款**的渲染调用（`ocr.render_page` / `ocr.scan_page_image`），
    否则量出来的数字不代表真实链路。
    """
    document = pymupdf.open(path)
    try:
        limit = pages if pages is not None else document.page_count
        pending: list[tuple[int, ocr.RenderedImage, list]] = []
        total = 0
        for index, page in enumerate(document, start=1):
            if index > limit:
                break
            classification = page_kind.classify_page(page)
            if not classification.page_ocr:
                continue
            image = ocr.scan_page_image(page) or ocr.render_page(page)
            total += len(image.pixels)
            pending.append((index, image, []))
        return pending, total
    finally:
        document.close()


def measure(path: Path, pages: int | None, *, recognize: bool) -> dict:
    """跑一遍完整链路并记录分段峰值。

    ``recognize=False`` 时只做渲染段 —— 用来把"渲染留下的内存"与"识别子进程的内存"
    分开：两者都计入进程峰值，但只有前者是这一侧能改的。
    """
    stages: list[Stage] = []

    started = time.time()
    pending, rendered = _render_all(path, pages)
    stages.append(
        Stage(
            name="渲染（全部页的 PNG 字节留在内存）",
            peak_seconds=time.time() - started,
            peak_rss_mb=peak_rss_bytes() / 1024 / 1024,
            pages=len(pending),
            rendered_bytes_mb=rendered / 1024 / 1024,
        )
    )

    recognized_pages = 0
    if recognize and pending:
        started = time.time()
        try:
            results = ocr.recognize([image for _, image, _ in pending])
            recognized_pages = len(results)
        except ocr.OcrUnavailable as error:
            results = []
            print(f"识别不可用，跳过识别段：{error}", file=sys.stderr)
        stages.append(
            Stage(
                name="识别（把上面那些字节分批送出去）",
                peak_seconds=time.time() - started,
                peak_rss_mb=peak_rss_bytes() / 1024 / 1024,
                pages=recognized_pages,
                rendered_bytes_mb=rendered / 1024 / 1024,
            )
        )

    del pending
    gc.collect()

    return {
        "document": str(path),
        "page_limit": pages,
        "peak_rss_mb": peak_rss_bytes() / 1024 / 1024,
        "rendered_bytes_mb": rendered / 1024 / 1024,
        "stages": [asdict(stage) for stage in stages],
    }


def measure_sections(path: Path, pages: int | None) -> dict:
    """跑真实的 `_pdf_sections`，量整条链路的峰值。

    这是唯一能反映生产行为的数字：它包含两遍处理、模板标记过滤、按块渲染与兜底逻辑。
    `measure()` 那两条分段只用于把"渲染留下的字节"与"识别子进程"分开归因。
    """
    from app.document.builtin_formats import MAX_RENDER_CHUNK

    document = pymupdf.open(path)
    try:
        if pages is not None and pages < document.page_count:
            document.select(range(pages))
        processed = document.page_count
        started = time.time()
        sections = _pdf_sections(document)
        elapsed = time.time() - started
    finally:
        document.close()
    peak = peak_rss_bytes() / 1024 / 1024
    return {
        "document": str(path),
        "mode": "sections",
        "pages": processed,
        "page_limit": pages,
        "chunk_pages": MAX_RENDER_CHUNK,
        "peak_rss_mb": peak,
        "elapsed_seconds": elapsed,
        "mb_per_page": peak / processed if processed else 0.0,
        "sections": len(sections),
        "figures": sum(len(section.figures) for section in sections),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, default=None, help="只处理前 N 页")
    parser.add_argument("--no-recognize", action="store_true", help="只跑渲染段")
    parser.add_argument(
        "--sections",
        action="store_true",
        help="跑真实的 _pdf_sections（两遍处理、按块渲染）",
    )
    parser.add_argument("--json", type=Path, default=None)
    arguments = parser.parse_args()

    if arguments.sections:
        report = measure_sections(arguments.pdf, arguments.pages)
        print(
            f"_pdf_sections（按块 {report['chunk_pages']} 页）  {report['elapsed_seconds']:>7.1f}s  "
            f"峰值 {report['peak_rss_mb']:>7.1f} MB  "
            f"（{report['pages']} 页、{report['sections']} 节、{report['figures']} 图）"
        )
        if arguments.json:
            arguments.json.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return 0
    report = measure(arguments.pdf, arguments.pages, recognize=not arguments.no_recognize)
    for stage in report["stages"]:
        print(
            f"{stage['name']:38} {stage['peak_seconds']:>7.1f}s  "
            f"峰值 {stage['peak_rss_mb']:>7.1f} MB  渲染字节 {stage['rendered_bytes_mb']:>7.1f} MB"
        )
    print(f"{'进程峰值':38} {'':>7}   峰值 {report['peak_rss_mb']:>7.1f} MB")
    if arguments.json:
        arguments.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
