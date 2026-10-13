"""图片文字识别：macOS 用系统 Vision，Windows 用系统 OCR，都不引入第三方 OCR 包。

**为什么走系统能力而不是 PaddleOCR/rapidocr。** 本仓库对运行时体积有过明确取舍：用
sqlite-vec 替掉 chromadb 省下 134M（见 ``backend/pyproject.toml`` 的注释）。OCR 库连带
模型动辄 200–500MB，会把省下的钱花回去。而两个平台都有随系统分发、离线、免费的识别
能力，质量足够 —— 实测 macOS Vision 对一张状态转移图（``p(1/00) = 0.2``、状态名
``00``/``01``）逐条识别正确，对中文扫描页也能给出可检索的正文。

**为什么是批量接口。** macOS 那条路每次调用要付约 0.9 秒的进程启动开销（实测：单张
0.90 秒、三张 1.63 秒）。一本 487 页的扫描书逐页调用要白等 7 分钟。所以接口一次接收
多张图，进程只起一次。

**为什么提供商要显式报告不可用。** 静默降级会让用户以为图片已经识别过了。这与
:class:`app.document.formats.DocumentFormat` 的 ``availability`` 同一条约定：能力要么
真的可用，要么给出原因，不存在「看起来做了但没做」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pymupdf

# 纯扫描件的判据住在 page_kind 里：那是「页型判定」的知识，取图与分类两边都要用同一套
# 门槛，放在任一侧都会让另一侧反向依赖。
from app.document.page_kind import (
    PURE_SCAN_MAX_DRAWINGS,
    PURE_SCAN_MIN_PIXELS_PER_POINT,
)

#: 批量调用单次处理多少张图。
#:
#: **这个数决定并行的粒度，不是越大越好。** 实测 48 页、4 并发：
#:
#: ==========  ==========  ===========
#: 每批张数     批次数       每页
#: ==========  ==========  ===========
#: 4           12          0.138s
#: 8           6           0.155s
#: 16          3           0.161s
#: 64          1           0.344s
#: ==========  ==========  ===========
#:
#: 原来的 64 让一整本书只剩 1 个批次，于是并发完全用不上 —— 那份设计的收益是 0。
#: 取 8：每页 2.2x，同时把并发进程里驻留的图片数压在 8 张（每张解码 27 MB）。
MAX_IMAGES_PER_CALL = 8
#: 并发识别的进程数上限，以及每批处理的图片数。
#:
#: 实测 64 页、10 核机器（每页耗时/加速比）：
#:
#: ==========  ==========  ==========  ==========
#: 并发         批次 8      批次 16     批次 4
#: ==========  ==========  ==========  ==========
#: 1（串行）    0.367s      —           —
#: 4           0.139s      0.138s      0.151s
#: 6           0.137s      0.153s      0.124s
#: 8           **0.111s**  0.147s      0.112s
#: 10          0.115s      0.156s      0.122s
#: ==========  ==========  ==========  ==========
#:
#: 8 是拐点（3.3x，串行基线 0.367s → 0.111s），10 反而回落 —— 超订用只会互相挤。
#: 批次 8 与 4 几乎同速，取 8：批次数少一半，进程启动开销减半。
MAX_OCR_WORKERS = 8
#: 一个识别进程处理 ``MAX_IMAGES_PER_CALL`` 张图时的峰值常驻内存（实测，见下表）。
#:
#: 这个数决定并发度能不能开满：每个子进程都要把整批图解码进内存。
#:
#: ==========  ==================
#: 每批张数     子进程峰值 RSS
#: ==========  ==================
#: 2           214 MB
#: 4           311 MB
#: 8           419 MB
#: 16          684 MB
#: 32          1159 MB
#: ==========  ==================
#:
#: 8 并发 × 419 MB ≈ 3.4 GB。16 GB 的机器没问题，8 GB 的机器会吃紧，所以并发度
#: 还要被可用内存二次约束（见 :func:`_worker_limit`），而不是只看核数。
WORKER_MEMORY_BYTES = 419 * 1024 * 1024
#: OCR 渲染整页时的 DPI。
#:
#: **150 是实测选出来的，不是拍的。** 用两本「扫描 + 已有 OCR 文字层」的书做参照，
#: 量字符错误率（CER = 编辑距离 / 参考长度，去掉空白后计算）：
#:
#: ==============  =========  =========  =========
#: 书              150        200        300
#: ==============  =========  =========  =========
#: 算法导论         0.1736     0.1749     0.1758
#: Linear Algebra  0.1286     0.1315     0.1329
#: ==============  =========  =========  =========
#:
#: 逐页配对比较给出的方向是「更高 DPI 略差」：300 相比 150，算法导论 8 页更差 4 页更好、
#: Linear Algebra 10 页更差 8 页更好。差异量级 0.2–0.4 个百分点（每千字 2–4 个字符），
#: 而 300 dpi 的渲染时间接近 150 的两倍。
#:
#: 所以 150：内容与 300 不可区分，渲染开销约为其一半。证据在
#: `docs/ocr-dpi-accuracy.json`，量法在 `scripts/measure_ocr_accuracy.py`。
#:
#: **CER 不是绝对准确率**：参考文本来自旧 OCR 文字层，本身有错，13–18% 的分歧里包含
#: 参考自己的错误（这类书上的 Vision 甚至可能比原文字层更准）。能支持的是**相对结论**：
#: 这个区间内 DPI 不影响质量。
PAGE_RENDER_DPI = 150


class OcrUnavailable(RuntimeError):
    """这台机器没有可用的识别后端，或后端拒绝了这个输入。"""


@dataclass(frozen=True)
class OcrLine:
    """一行识别结果。坐标是**相对于该图片**的 PDF 坐标（原点左上，单位点）。"""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float]

    @property
    def center_y(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2


@dataclass(frozen=True)
class OcrPage:
    """一张图的识别结果。``lines`` 按阅读顺序排序。"""

    index: int
    width: float
    height: float
    lines: tuple[OcrLine, ...]

    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class RenderedImage:
    """一张待识别的图：内存里的像素 + 它在文档坐标系里的位置。"""

    pixels: bytes
    #: 图片本身的宽高（点），用来把识别结果的坐标换算回文档坐标。
    width: float
    height: float
    #: 这张图在页面上占的位置（PDF 坐标）。整页 OCR 时等于页面矩形。
    page_rect: tuple[float, float, float, float]
    page_number: int
    #: 容器格式，决定写临时文件时的扩展名。
    #:
    #: 必须跟着字节走：从扫描件里直抽出来的内嵌位图常常是 JPEG，写成 `.png` 会让识别
    #: 后端按 PNG 解析而失败。而按格式落盘还省掉了「转成 PNG」那一步 —— 实测转一次要
    #: 71 ms/页，占直抽路径的大头。返回给用户看的图另说，那一步统一转 PNG。
    extension: str = "png"


class OcrBackend(Protocol):
    """一个识别后端。"""

    name: str

    def recognize(self, paths: Sequence[Path], sizes: Sequence[tuple[float, float]]) -> list[OcrPage]:
        """识别一批图片，返回值与输入顺序一致。"""
        ...


def render_page(page: pymupdf.Page) -> RenderedImage:
    """把整页渲染成图片，供 OCR 使用。

    渲染而不是直接取内嵌位图：扫描件的内嵌位图常常是被裁切或分块存放的，直接取字节
    拿不到完整页面。渲染保证「看到的就是这一页」。
    """
    pixmap = page.get_pixmap(dpi=PAGE_RENDER_DPI)
    rect = page.rect
    return RenderedImage(
        pixels=pixmap.tobytes("png"),
        width=float(pixmap.width),
        height=float(pixmap.height),
        page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
        page_number=page.number + 1,
    )


def render_region(page: pymupdf.Page, rect: pymupdf.Rect, *, dpi: int = PAGE_RENDER_DPI) -> RenderedImage:
    """把页面的一块区域渲染成图片。

    用于只识别插图区域，而不是整页。保留 2 倍余量：插图里的字号通常比正文小。
    """
    pixmap = page.get_pixmap(dpi=dpi * 2, clip=rect)
    return RenderedImage(
        pixels=pixmap.tobytes("png"),
        width=float(pixmap.width),
        height=float(pixmap.height),
        page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
        page_number=page.number + 1,
    )



def scan_page_image(page: pymupdf.Page) -> RenderedImage | None:
    """纯扫描页：直接用它的原生位图，不做渲染。

    见 :func:`page_scan_image_payload` 的实测数字与判据。返回的 ``extension`` 是内嵌
    位图的真实格式（实测这类书多为 JPEG），识别后端按原格式读取，省掉转 PNG 的 71 ms/页。
    """
    extracted = page_scan_image_payload(page)
    if extracted is None:
        return None
    payload, extension, width, height = extracted
    rect = page.rect
    return RenderedImage(
        pixels=payload,
        width=float(width),
        height=float(height),
        page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
        page_number=page.number + 1,
        extension=extension,
    )


def page_scan_image_payload(page: pymupdf.Page) -> tuple[bytes, str, int, int] | None:
    """这一页若是纯扫描件，返回它的原生位图 ``(字节, 扩展名, 宽, 高)``。

    **为什么要单独走这条路。** 整页扫描件上「渲染页面」是白做的：页面内容就是那一张
    位图，而渲染等于把它重新光栅化一遍 —— 还带上 PNG 编码、写临时文件、子进程再读回来。
    实测一本扫描教材的 18 页：抽出内容相差 0.2%（噪声量级），耗时快 5 倍。而且直抽通常
    **更忠实**：这类书的内嵌位图是原生分辨率（实测 96 dpi），按 150 dpi 渲染是在插值放大，
    多出来的像素不是信息。

    **判据刻意只用便宜查询。** ``get_image_info`` 实测 36–142 ms/页，而它提供的信息这里
    大部分可以不要：

    * ``get_images``（0.1 ms）给出图的数量与 xref。
    * ``get_drawings``（≈0 ms）给出矢量路径条数 —— 页面上还有矢量内容时，那些内容不在
      位图里，直抽会丢掉它们。
    * ``extract_image`` 给出真实像素尺寸，用它算「每点几个像素」判断这图够不够整页。

    三个便宜查询加起来 15.1 ms/页，而走一次 ``get_image_info`` 的路径要 51.0 ms/页 ——
    **3.4 倍差距**，且实测两者在这本书上都是 122/122 命中。

    **只在真正纯扫描时才用。** 判不准时返回 ``None``，调用方退回渲染 —— 那是安全的一侧。
    """
    page_width = abs(page.rect.width)
    if not page_width:
        return None
    drawing_count = len(page.get_drawings())
    if drawing_count > PURE_SCAN_MAX_DRAWINGS:
        return None
    graphics = page.get_images(full=True)
    if len(graphics) != 1:
        # 0 张 = 没有位图可抽；2 张以上 = 叠图（Feynman 那种形态），哪张是内容无法判断。
        return None
    xref = int(graphics[0][0])
    if not xref:
        return None
    try:
        extracted = page.parent.extract_image(xref)
    except (RuntimeError, ValueError, KeyError):
        return None
    payload = extracted.get("image")
    extension = extracted.get("ext")
    width = int(extracted.get("width") or 0)
    height = int(extracted.get("height") or 0)
    if not payload or not extension or width <= 0 or height <= 0:
        return None
    # 每点像素数：低于门槛说明这张图只是页面上的一个小插图，抽它会丢掉其余内容。
    # 实测纯扫描件的该值都在 1.3 以上，门槛 0.5 留了足够余量。
    if width / page_width < PURE_SCAN_MIN_PIXELS_PER_POINT:
        return None
    return payload, str(extension), width, height


def render_pages(pages: Sequence[pymupdf.Page], *, dpi: int | None = None) -> list[RenderedImage]:
    """并发渲染多页整页图。

    **渲染曾经是第二大开销，并行化识别之后它变成第一大。** 实测一本扫描书的中段：
    渲染（串行）20.8s = 47.4%，识别（8 并发）18.3s = 41.6%，分类 4.8s = 10.9%。
    只并行识别等于把最长的那块留在单线程里。

    PyMuPDF 的 `get_pixmap` 在原生代码里执行并释放 GIL，所以线程池对它是有效并行。
    用线程而不是进程：渲染结果（每页约 1 MB PNG）要回传给主线程，走进程还得再序列化一遍。

    并发度与识别共用 :func:`_worker_limit`，并且在这里是安全的：渲染本身只产 PNG 字节，
    解码后的大块内存（27 MB/页）由子进程持有，本进程这边只有压缩后的字节。
    """
    if not pages:
        return []
    workers = min(_worker_limit(), len(pages))
    if workers <= 1:
        return [render_page(page) for page in pages]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda page: render_page(page), pages))


def _to_pdf_bbox(
    x: float, y: float, width: float, height: float, size: tuple[float, float]
) -> tuple[float, float, float, float]:
    """把归一化的左下原点坐标换算成像素坐标的点值，原点左上。

    Vision 给的是 0…1 的归一化矩形、原点在**左下**；Windows OCR 给的是像素、原点在
    左上。两者都经这里归一，调用方只面对一种坐标。
    """
    image_width, image_height = size
    left = x * image_width
    # 左下原点 → 左上原点：矩形的「下边」变成距顶部的距离。
    top = (1.0 - (y + height)) * image_height
    return (left, top, left + width * image_width, top + height * image_height)


class VisionOcr:
    """macOS：系统自带的 Vision 框架。

    通过一个随包分发的小程序（``vision_ocr``，由 ``vision_ocr.swift`` 编译而来）调用。
    编译产物约 98 KB、只链系统框架，不需要用户安装 Swift 工具链 —— 这一点与 pyobjc
    路线不同，也是选它的原因之一。
    """

    name = "macos-vision"

    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def recognize(self, paths: Sequence[Path], sizes: Sequence[tuple[float, float]]) -> list[OcrPage]:
        """并发识别。

        **识别本身是串行之外唯一的瓶颈**：实测渲染只占 7–36% 的时间，剩下都是 Vision
        在跑，而它对同一份输入是确定的 —— 想更快只能并行开进程。实测 20 页：

        ==========  ===========  =======
        并发度       每页         加速
        ==========  ===========  =======
        串行         0.301s       —
        2 进程       0.182s       1.65x
        4 进程       0.136s       2.22x
        ==========  ===========  =======

        并发度上限刻意压在 :data:`MAX_OCR_WORKERS`：单页渲染图解码成 RGBA 是 27 MB
        （实测 2249×3180），开太多进程先撞的是内存而不是 CPU 上限。
        """
        if not paths:
            return []
        batches = [
            (paths[start : start + MAX_IMAGES_PER_CALL], sizes[start : start + MAX_IMAGES_PER_CALL])
            for start in range(0, len(paths), MAX_IMAGES_PER_CALL)
        ]
        workers = _worker_limit()
        if len(batches) == 1 or workers <= 1:
            pages: list[OcrPage] = []
            for batch, batch_sizes in batches:
                pages.extend(self._recognize_batch(batch, batch_sizes))
            return pages

        # 用线程而不是 asyncio：等待的是子进程，`subprocess.run` 在等待期间释放 GIL，
        # 而且这里不需要事件循环，把一个同步模块塞进异步只会让调用方多一层负担。
        #
        # 失败处理：先让**所有**批次跑完再抛第一个错误。第一次失败就 `shutdown(
        # cancel_futures=True)` 会留下仍在运行的子进程，那些进程还会往临时目录写文件 ——
        # 而临时目录在 `recognize()` 的 with 块退出时就被删了。
        with ThreadPoolExecutor(max_workers=min(workers, len(batches))) as pool:
            futures = [
                pool.submit(self._recognize_batch, batch, batch_sizes)
                for batch, batch_sizes in batches
            ]
            results: list[list[OcrPage]] = []
            failure: Exception | None = None
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as error:  # noqa: BLE001 - 收集后统一抛，见上
                    results.append([])
                    if failure is None:
                        failure = error
            if failure is not None:
                raise failure
        return [page for batch_pages in results for page in batch_pages]

    def _recognize_batch(
        self, paths: Sequence[Path], sizes: Sequence[tuple[float, float]]
    ) -> list[OcrPage]:
        try:
            completed = subprocess.run(
                [str(self.executable), *[str(path) for path in paths]],
                capture_output=True,
                check=False,
                timeout=_timeout_for(len(paths)),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise OcrUnavailable(f"调用系统识别失败：{error}") from error
        if completed.returncode != 0:
            reason = completed.stderr.decode("utf-8", "replace").strip()[:200]
            raise OcrUnavailable(f"系统识别返回 {completed.returncode}：{reason}")

        # JSON Lines：每张图一行，文件损坏时那一行为空而不是整批失败。
        results: dict[int, list[OcrLine]] = {}
        for raw in completed.stdout.decode("utf-8", "replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            index = int(payload.get("index", -1))
            if not 0 <= index < len(paths):
                continue
            size = sizes[index]
            results[index] = [
                OcrLine(
                    text=item["text"],
                    confidence=float(item.get("confidence", 0.0)),
                    bbox=_to_pdf_bbox(
                        float(item["x"]),
                        float(item["y"]),
                        float(item["width"]),
                        float(item["height"]),
                        size,
                    ),
                )
                for item in payload.get("lines", [])
            ]
        return [
            OcrPage(index=index, width=sizes[index][0], height=sizes[index][1],
                    lines=_reading_order(results.get(index, [])))
            for index in range(len(paths))
        ]


def _physical_memory_bytes() -> int:
    """物理内存总量，取不到时返回 0（表示「不知道」，按最保守处理）。"""
    import sys

    try:
        if sys.platform == "darwin":
            completed = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, check=False, timeout=10.0
            )
            if completed.returncode == 0:
                return int(completed.stdout.decode().strip())
        elif sys.platform == "win32":
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
                ],
                capture_output=True,
                check=False,
                timeout=20.0,
            )
            if completed.returncode == 0:
                return int(completed.stdout.decode().strip())
        else:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0
    return 0


def _worker_limit() -> int:
    """能开几个识别进程：受核数与内存同时约束。

    只看核数会在小内存机器上把内存吃光 —— 每个识别进程要吞掉整批解码后的图（批次 8 时
    实测 419 MB），8 个就是 3.4 GB。留出余量：识别最多用物理内存的 1/4，剩下留给
    Python 侧的渲染图、向量索引与 Electron 渲染进程。

    内存取不到时按 **2** 处理：这类探测在打包环境里可能失败，失败时宁可慢也不要
    在用户的机器上触发内存压力。
    """
    import os

    cores = os.cpu_count() or 1
    total = _physical_memory_bytes()
    if not total:
        return min(MAX_OCR_WORKERS, cores, 2)
    by_memory = max(1, int(total / 4 / WORKER_MEMORY_BYTES))
    return max(1, min(MAX_OCR_WORKERS, cores, by_memory))


def _timeout_for(count: int) -> float:
    """识别超时：启动开销 + 每张的余量。宁可超时也不要挂住导入任务。"""
    return 60.0 + 20.0 * count


def _reading_order(lines: Iterable[OcrLine]) -> tuple[OcrLine, ...]:
    """把识别结果排成阅读顺序。

    Vision 的返回顺序不是阅读顺序（实测同一张图里 ``p(1/00)`` 与 ``01`` 交错输出）。
    直接按返回顺序拼接会得到语义错乱的一串；按「先上后下、同一行内先左后右」排序后
    拼出的文本才接近人读的顺序。
    """
    ordered = sorted(lines, key=lambda line: (round(line.center_y / 6.0), line.center_x))
    return tuple(ordered)


def _vision_executable() -> Path | None:
    """找到识别程序。

    只找随包分发的那个：仓库存的是 `.swift` 源码，二进制由
    ``scripts/build-vision-ocr.sh`` 在打包（或开发环境准备）时生成。
    """
    candidate = Path(__file__).parent / "vision_ocr"
    return candidate if candidate.is_file() else None


#: 探针结果缓存。导入对话框每次打开都会问「能不能用」，而探针要起一个进程 ——
#: 不缓存的话每开一次对话框都多花几十毫秒，而二进制在进程生命周期内不会换。
_vision_probe: tuple[bool, str | None] | None = None


def _probe_vision(executable: Path) -> tuple[bool, str | None]:
    """执行 `--probe`，确认这个二进制在**本机**真的能跑。

    只看「文件存在且可执行」是不够的：arm64 二进制在 Intel Mac 上两个条件都满足，
    但一执行就被系统拒绝（bad CPU type）。那种情况必须在能力检查阶段就报出来，
    而不是让用户等到导入一份 500 页的书时才发现。

    失败原因里带上系统给的原文 —— 「bad CPU type」这几个字正是用户拿去搜索的词。
    """
    try:
        completed = subprocess.run(
            [str(executable), "--probe"],
            capture_output=True,
            check=False,
            timeout=30.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"识别程序无法执行：{error}"
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:160]
        return False, f"识别程序执行失败（{completed.returncode}）：{detail}"
    return True, None


def vision_availability() -> str | None:
    """macOS Vision 是否可用；不可用时返回原因。"""
    global _vision_probe
    import sys

    if sys.platform != "darwin":
        return "当前系统不是 macOS"
    executable = _vision_executable()
    if executable is None:
        return "缺少识别程序，请先在打包或开发环境准备时运行 scripts/build-vision-ocr.sh"
    if not os.access(executable, os.X_OK):
        return "识别程序没有执行权限"
    if _vision_probe is None:
        _vision_probe = _probe_vision(executable)
    ok, reason = _vision_probe
    return None if ok else reason


#: Windows 的系统 OCR 脚本。走 PowerShell 是因为 ``Windows.Media.Ocr`` 只对
#: WinRT 调用方开放，而 PowerShell 是系统自带、无需安装的 WinRT 宿主。
#: 与 macOS 那条路保持同一套输出格式（JSON Lines、归一化坐标可省），便于上游统一处理。
_WINDOWS_OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($operation) {
    $task = $asTask.MakeGenericMethod($operation.GetType().GenericTypeArguments[0]).Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { Write-Error '系统没有安装可用的 OCR 语言包'; exit 2 }

$index = 0
foreach ($path in $args) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path))
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read))
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream))
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync())
    $result = Await ($engine.RecognizeAsync($bitmap))
    $lines = @()
    # Windows OCR 的坐标是像素、原点左上，与 PDF 一致，这里只做线性换算交给上游。
    foreach ($line in $result.Lines) {
        $words = @($line.Words)
        if ($words.Count -eq 0) { continue }
        $x0 = ($words | Measure-Object -Property { $_.BoundingRect.X } -Minimum).Minimum
        $y0 = ($words | Measure-Object -Property { $_.BoundingRect.Y } -Minimum).Minimum
        $x1 = ($words | Measure-Object -Property { $_.BoundingRect.X + $_.BoundingRect.Width } -Maximum).Maximum
        $y1 = ($words | Measure-Object -Property { $_.BoundingRect.Y + $_.BoundingRect.Height } -Maximum).Maximum
        $lines += [pscustomobject]@{
            text = $line.Text; confidence = 1.0
            x = [double]$x0; y = [double]$y0
            width = [double]($x1 - $x0); height = [double]($y1 - $y0)
            pixel = $true
        }
    }
    [pscustomobject]@{ index = $index; lines = $lines } | ConvertTo-Json -Compress -Depth 6
    $index++
    $stream.Dispose()
}
"""


class WindowsOcr:
    """Windows：系统自带的 ``Windows.Media.Ocr``。

    **未在真机验证。** 开发环境是 macOS，这条路径按微软文档实现，但没有在 Windows 上
    跑过。``confidence`` 恒为 1.0 是因为这个 API 不返回置信度 —— 不要把它当成质量信号。
    """

    name = "windows-media-ocr"

    def __init__(self, powershell: str) -> None:
        self.powershell = powershell

    def recognize(self, paths: Sequence[Path], sizes: Sequence[tuple[float, float]]) -> list[OcrPage]:
        pages: list[OcrPage] = []
        for start in range(0, len(paths), MAX_IMAGES_PER_CALL):
            batch = paths[start : start + MAX_IMAGES_PER_CALL]
            pages.extend(self._recognize_batch(batch, sizes[start : start + MAX_IMAGES_PER_CALL]))
        return pages

    def _recognize_batch(
        self, paths: Sequence[Path], sizes: Sequence[tuple[float, float]]
    ) -> list[OcrPage]:
        script = Path(tempfile.mkdtemp(prefix="docmind-ocr-")) / "ocr.ps1"
        script.write_text(_WINDOWS_OCR_SCRIPT, encoding="utf-8")
        try:
            completed = subprocess.run(
                [
                    self.powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    *[str(path) for path in paths],
                ],
                capture_output=True,
                check=False,
                timeout=_timeout_for(len(paths)),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise OcrUnavailable(f"调用系统识别失败：{error}") from error
        if completed.returncode != 0:
            reason = completed.stderr.decode("utf-8", "replace").strip()[:200]
            raise OcrUnavailable(f"系统识别返回 {completed.returncode}：{reason}")

        results: dict[int, list[OcrLine]] = {}
        for raw in completed.stdout.decode("utf-8", "replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            index = int(payload.get("index", -1))
            if not 0 <= index < len(paths):
                continue
            size = sizes[index]
            # Windows OCR 给的是图片像素坐标，缩放到点值。
            scale_x = size[0] / max(payload.get("imageWidth", size[0]), 1)
            results[index] = [
                OcrLine(
                    text=item["text"],
                    confidence=float(item.get("confidence", 1.0)),
                    bbox=(
                        float(item["x"]) * scale_x,
                        float(item["y"]) * scale_x,
                        (float(item["x"]) + float(item["width"])) * scale_x,
                        (float(item["y"]) + float(item["height"])) * scale_x,
                    ),
                )
                for item in payload.get("lines", [])
            ]
        return [
            OcrPage(index=index, width=sizes[index][0], height=sizes[index][1],
                    lines=_reading_order(results.get(index, [])))
            for index in range(len(paths))
        ]


def windows_availability() -> str | None:
    """Windows 系统 OCR 是否可用；不可用时返回原因。"""
    import sys

    if sys.platform != "win32":
        return "当前系统不是 Windows"
    if shutil.which("powershell") is None:
        return "找不到 powershell"
    return None


def find_backend() -> OcrBackend | None:
    """挑一个可用的后端，都不可用时返回 ``None``。

    顺序固定：先 macOS Vision（已在真机验证），再 Windows。两个都不可用时返回 None 而
    不是抛错 —— 调用方要能在没有 OCR 的机器上照常导入文档，只是没有图片文字。
    """
    if vision_availability() is None:
        executable = _vision_executable()
        if executable is not None:
            return VisionOcr(executable)
    if windows_availability() is None:
        powershell = shutil.which("powershell")
        if powershell is not None:
            return WindowsOcr(powershell)
    return None


def unavailable_reason() -> str | None:
    """都没有可用后端时，给出**按平台**的原因，用于向用户解释。"""
    import sys

    if sys.platform == "darwin":
        return vision_availability()
    if sys.platform == "win32":
        return windows_availability()
    return "当前系统没有可用的图片文字识别能力"


#: 关掉图片文字识别的环境变量。设为 ``0``/``false``/``no``/``off`` 时 :func:`recognize`
#: 直接返回空结果，不做任何识别。
#:
#: **为什么用环境变量而不是参数。** 识别的调用点有四条通路（PDF 原生页补识别、扫描页、
#: Word、网页），要传参数就得改 `FormatRegistry.parse` 的签名与三个格式函数 —— 而那是个
#: 插件也要实现的契约。这里是一个进程级的行为开关，由导入服务在解析**之前**按用户设置
#: 落定（见 `app/imports/service.py`），进程级刚好是它该有的粒度。
#:
#: 关掉的代价必须是用户可见的：一份 53 页的截图型课件关掉之后 sections 从 53 降到 36、
#: 正文从 23447 字符降到 15486 —— 那 17 页没有文字层，识别是它们唯一的文字来源。
FIGURE_OCR_ENV = "DOCMIND_FIGURE_OCR"


def figure_ocr_enabled() -> bool:
    """当前是否允许识别图片里的文字。默认允许。"""
    value = os.environ.get(FIGURE_OCR_ENV)
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def recognize(images: Sequence[RenderedImage]) -> list[OcrPage]:
    """识别一批图片。

    ``images`` 为空时返回空列表（不是错误）：一份文档没有任何需要识别的图是正常情况。

    **关掉识别时返回空列表，而不是抛 `OcrUnavailable`。** 两者含义不同：不可用是"这台机器
    做不到"，调用方会退回文字层兜底；关掉是"用户选择不做"，每一页都按"没有识别结果"处理
    正是预期行为 —— 抛异常会让调用方去走"能力缺失"的分支，那是错的语义。

    渲染文件写在**用完即删**的临时目录里。一本 487 页的扫描书按 200 dpi 渲染出来是几百
    MB 的 PNG，留着不放会一直涨 —— 这类泄漏要攒够几本书才会被注意到。
    """
    if not images or not figure_ocr_enabled():
        return []
    backend = find_backend()
    if backend is None:
        raise OcrUnavailable(unavailable_reason() or "没有可用的识别后端")

    with tempfile.TemporaryDirectory(prefix="docmind-ocr-images-") as directory:
        paths: list[Path] = []
        sizes: list[tuple[float, float]] = []
        for index, image in enumerate(images):
            extension = image.extension.lstrip(".") or "png"
            path = Path(directory) / f"{index:05d}.{extension}"
            path.write_bytes(image.pixels)
            paths.append(path)
            sizes.append((image.width, image.height))
        return backend.recognize(paths, sizes)


def remap_to_page(page: OcrPage, image: RenderedImage) -> list[OcrLine]:
    """把「图片坐标系」的识别结果换算到「页面坐标系」。

    整页渲染时两者是同一个矩形，缩放为 1；识别插图区域时图片被放大过，必须按比例换算
    回页面坐标，否则引用定位会指到错误的位置。
    """
    left, top, right, bottom = image.page_rect
    page_width = right - left
    page_height = bottom - top
    if page_width <= 0 or page_height <= 0:
        return list(page.lines)
    scale_x = page_width / image.width if image.width else 1.0
    scale_y = page_height / image.height if image.height else 1.0
    return [
        OcrLine(
            text=line.text,
            confidence=line.confidence,
            bbox=(
                left + line.bbox[0] * scale_x,
                top + line.bbox[1] * scale_y,
                left + line.bbox[2] * scale_x,
                top + line.bbox[3] * scale_y,
            ),
        )
        for line in page.lines
    ]
