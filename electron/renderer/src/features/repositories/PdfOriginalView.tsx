import { ChevronLeft, ChevronRight, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import { clientErrorMessage } from "../settings/settings.queries";
import {
  createOriginalRangeTransport,
  decodeBase64Bytes,
  firstChunkRange,
} from "./pdf-range-transport";
import { getDocument } from "./pdf-runtime";

const MIN_SCALE = 0.5;
const MAX_SCALE = 4;
const ZOOM_STEP = 1.2;
/** 滚动容器左右留白，算「适合宽度」时要扣掉。 */
const PAGE_GUTTER = 28;
/** 预览区域外多大范围就开始栅格化，避免快速滚动时出现空白。 */
const RENDER_MARGIN = "320px";
/** 尚未测出尺寸时的兜底页尺寸（US Letter @72dpi）。 */
const FALLBACK_PAGE = { width: 612, height: 792 };

type PageSize = { width: number; height: number };

type PdfOriginalViewProps = {
  documentId: string;
  byteSize: number;
};

/**
 * PDF 原件阅读视图。
 *
 * 与「解析」视图的分工：这里回答「文档长什么样」，不做内容提取。
 * 字节全部经 IPC 按 Range 拉取（见 pdf-range-transport），
 * 渲染层因此不需要后端地址、令牌，也不需要放开任何白名单。
 */
export function PdfOriginalView({ documentId, byteSize }: PdfOriginalViewProps) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [failure, setFailure] = useState("");
  const [pageCount, setPageCount] = useState(0);
  const [current, setCurrent] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize | null>(null);
  const [fitWidth, setFitWidth] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [available, setAvailable] = useState(0);
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let disposed = false;
    let task: PDFDocumentLoadingTask | null = null;
    setPdf(null);
    setPageCount(0);
    setFailure("");
    setCurrent(1);
    setPageSize(null);

    void (async () => {
      try {
        const [begin, end] = firstChunkRange(byteSize);
        const first = await window.docmind.documents.readOriginalChunk(documentId, begin, end);
        if (disposed) return;
        const transport = createOriginalRangeTransport({
          total: first.total || byteSize,
          initialData: decodeBase64Bytes(first.data),
          readChunk: (chunkBegin, chunkEnd) =>
            window.docmind.documents.readOriginalChunk(documentId, chunkBegin, chunkEnd),
          onError: (error) => {
            if (!disposed) setFailure(clientErrorMessage(error));
          },
        });
        task = getDocument({ range: transport });
        const loaded = await task.promise;
        if (disposed) {
          void loaded.destroy();
          return;
        }
        // 用第 1 页的尺寸给所有页面占位：逐页测量意味着打开文档就要
        // 拉取每一页的字典，对上百页的文档是不可接受的开销。
        const viewport = (await loaded.getPage(1)).getViewport({ scale: 1 });
        if (disposed) {
          void loaded.destroy();
          return;
        }
        setPageSize({ width: viewport.width, height: viewport.height });
        setPageCount(loaded.numPages);
        setPdf(loaded);
      } catch (error) {
        if (!disposed) setFailure(clientErrorMessage(error));
      }
    })();

    return () => {
      disposed = true;
      void task?.destroy();
    };
  }, [documentId, byteSize]);

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;
    const measure = () => setAvailable(node.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const page = pageSize ?? FALLBACK_PAGE;
  const scale = useMemo(() => {
    if (fitWidth && available > 0) {
      return clamp((available - PAGE_GUTTER * 2) / page.width);
    }
    return clamp(zoom);
  }, [fitWidth, available, zoom, page.width]);

  const scaled = useMemo(
    () => ({ width: page.width * scale, height: page.height * scale }),
    [page.width, page.height, scale],
  );

  const syncCurrentPage = useCallback(() => {
    const node = scrollerRef.current;
    if (!node) return;
    let nearest = 1;
    let smallest = Number.POSITIVE_INFINITY;
    const wrappers = node.querySelectorAll<HTMLElement>("[data-pdf-page]");
    // 用下标遍历：tsconfig 没开 DOM.Iterable，NodeList 没有 Symbol.iterator。
    for (let index = 0; index < wrappers.length; index += 1) {
      const wrapper = wrappers.item(index);
      if (!wrapper) continue;
      const distance = Math.abs(wrapper.offsetTop - node.scrollTop);
      if (distance < smallest) {
        smallest = distance;
        nearest = Number(wrapper.dataset.pdfPage);
      }
    }
    setCurrent(nearest);
  }, []);

  const goToPage = useCallback((target: number) => {
    const node = scrollerRef.current;
    if (!node) return;
    const bounded = Math.min(
      Math.max(target, 1),
      Math.max(node.querySelectorAll("[data-pdf-page]").length, 1),
    );
    const wrapper = node.querySelector<HTMLElement>(`[data-pdf-page="${bounded}"]`);
    if (wrapper) {
      node.scrollTo({ top: wrapper.offsetTop - 12, behavior: "smooth" });
      setCurrent(bounded);
    }
  }, []);

  if (failure) {
    return (
      <div className="pdf-original" role="status">
        <p className="pdf-original-error">{failure}</p>
      </div>
    );
  }

  if (!pdf) {
    return (
      <div className="pdf-original" role="status">
        <p className="pdf-original-loading">正在加载原件…</p>
      </div>
    );
  }

  return (
    <div className="pdf-original">
      <div className="pdf-toolbar" role="toolbar" aria-label="原件阅读工具">
        <div className="pdf-toolbar-group">
          <button
            aria-label="上一页"
            className="tree-icon-button"
            disabled={current <= 1}
            onClick={() => goToPage(current - 1)}
            title="上一页"
            type="button"
          >
            <ChevronLeft aria-hidden="true" size={16} />
          </button>
          <input
            aria-label="跳转到页"
            className="pdf-page-input"
            max={pageCount}
            min={1}
            onChange={(event) => {
              const parsed = Number.parseInt(event.target.value, 10);
              if (Number.isFinite(parsed)) goToPage(parsed);
            }}
            type="number"
            value={current}
          />
          <span className="pdf-page-total">/ {pageCount}</span>
          <button
            aria-label="下一页"
            className="tree-icon-button"
            disabled={current >= pageCount}
            onClick={() => goToPage(current + 1)}
            title="下一页"
            type="button"
          >
            <ChevronRight aria-hidden="true" size={16} />
          </button>
        </div>
        <div className="pdf-toolbar-group">
          <button
            aria-label="缩小"
            className="tree-icon-button"
            disabled={!fitWidth && zoom <= MIN_SCALE}
            onClick={() => {
              setFitWidth(false);
              setZoom(clamp(scale / ZOOM_STEP));
            }}
            title="缩小"
            type="button"
          >
            <Minus aria-hidden="true" size={16} />
          </button>
          <span className="pdf-zoom-label">{Math.round(scale * 100)}%</span>
          <button
            aria-label="放大"
            className="tree-icon-button"
            disabled={!fitWidth && zoom >= MAX_SCALE}
            onClick={() => {
              setFitWidth(false);
              setZoom(clamp(scale * ZOOM_STEP));
            }}
            title="放大"
            type="button"
          >
            <Plus aria-hidden="true" size={16} />
          </button>
          <button
            aria-pressed={fitWidth}
            className="button button-secondary pdf-fit-button"
            onClick={() => setFitWidth(true)}
            type="button"
          >
            适合宽度
          </button>
        </div>
      </div>
      <div className="pdf-scroller" onScroll={syncCurrentPage} ref={scrollerRef}>
        {Array.from({ length: pageCount }, (_, index) => (
          <div
            className="pdf-page"
            data-pdf-page={index + 1}
            key={index + 1}
            style={{ height: scaled.height, width: scaled.width }}
          >
            <PdfPageCanvas pageNumber={index + 1} pdf={pdf} scale={scale} width={scaled.width} />
          </div>
        ))}
      </div>
    </div>
  );
}

function clamp(value: number): number {
  return Math.min(Math.max(value, MIN_SCALE), MAX_SCALE);
}

function PdfPageCanvas({
  pdf,
  pageNumber,
  scale,
  width,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  width: number;
}) {
  const holderRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const node = holderRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
      },
      { root: node.closest(".pdf-scroller"), rootMargin: RENDER_MARGIN },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    let disposed = false;
    let task: { cancel(): void; promise: Promise<unknown> } | null = null;
    let rendered: PDFPageProxy | null = null;

    void (async () => {
      try {
        const page = await pdf.getPage(pageNumber);
        if (disposed) return;
        const viewport = page.getViewport({ scale });
        // devicePixelRatio > 1 时按物理像素开画布，否则高分屏上字是模糊的。
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        task = page.render({
          canvasContext: context,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
          viewport,
        });
        rendered = page;
        await task.promise;
      } catch (error) {
        // 缩放切换会取消上一帧，这不是失败。
        if (!disposed && (error as { name?: string })?.name !== "RenderingCancelledException") {
          setFailed(true);
        }
      }
    })();

    return () => {
      disposed = true;
      task?.cancel();
      rendered?.cleanup();
    };
  }, [pdf, pageNumber, scale, visible]);

  return (
    <div className="pdf-page-canvas" ref={holderRef} style={{ width }}>
      {failed ? <p className="pdf-page-failed">第 {pageNumber} 页渲染失败</p> : null}
      <canvas hidden={failed} ref={canvasRef} />
    </div>
  );
}
