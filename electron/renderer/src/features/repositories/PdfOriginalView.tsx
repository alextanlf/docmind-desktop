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
/**
 * 手动输入 / 方向键的百分比步长。
 *
 * 5 而不是 10：常见阅读器的档位间隔就是 25%，5 的倍数能保证从任意当前值都能落到
 * 档位上（100 → 105 → … → 125），步长 10 遇到 103 这种值就永远差一点。
 */
const ZOOM_PERCENT_STEP = 5;
/**
 * 预设档位。与常见 PDF 阅读器（预览 / Acrobat / Chrome）取同一组百分比：用户在这里
 * 记住的是档位本身，不是本应用特有的数字。
 */
const ZOOM_PERCENT_PRESETS = [50, 75, 100, 125, 150, 200, 300, 400] as const;
/** 当前的缩放是怎么定出来的。 */
const FIT_WIDTH = "fit-width";
const FIT_PAGE = "fit-page";
/** 既不适合宽度也不适合页面：比例就是用户自己给的那个值。 */
const FREE_ZOOM = "free";
const CUSTOM_VALUE = "custom";
type ZoomMode = typeof FIT_WIDTH | typeof FIT_PAGE | typeof FREE_ZOOM;
/**
 * 滚动容器左右留白，算「适合宽度 / 适合页面」时要扣掉。
 *
 * 这与纵向留白都来自一条规则（`.pdf-scroller { padding: 20px 0 40px }`）：横向的 28
 * 是页面自身 `margin: 0 auto` 之外留的视觉余量，纵向的上 20 / 下 40 就是后者。改那条
 * CSS 就得改这两个常量，否则"适合页面"会把页面算得比可视区还高、一屏放不下。
 */
const PAGE_GUTTER = 28;
const PAGE_GUTTER_VERTICAL = 60;
/** 预览区域外多大范围就开始栅格化，避免快速滚动时出现空白。 */
const RENDER_MARGIN = "320px";
/** 尚未测出尺寸时的兜底页尺寸（US Letter @72dpi）。 */
const FALLBACK_PAGE = { width: 612, height: 792 };

type PageSize = { width: number; height: number };

type PdfOriginalViewProps = {
  documentId: string;
  byteSize: number;
  /**
   * 要定位并高亮的图。
   *
   * `bbox` 是 PDF 点、原点左上的矩形（与后端 `ParsedFigure.bbox` 同一坐标系）。
   * 为 null 时表示「只跳到这一页，不框任何东西」。
   */
  locate?: { page: number; bbox: [number, number, number, number] | null } | null;
};

/**
 * PDF 原件阅读视图。
 *
 * 与「解析」视图的分工：这里回答「文档长什么样」，不做内容提取。
 * 字节全部经 IPC 按 Range 拉取（见 pdf-range-transport），
 * 渲染层因此不需要后端地址、令牌，也不需要放开任何白名单。
 */
export function PdfOriginalView({ documentId, byteSize, locate }: PdfOriginalViewProps) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [failure, setFailure] = useState("");
  const [pageCount, setPageCount] = useState(0);
  // 打开就落在目标页：从解析视图点图进来时，用户要看的正是那一页。
  const [current, setCurrent] = useState(() => Math.max(locate?.page ?? 1, 1));
  const [pageSize, setPageSize] = useState<PageSize | null>(null);
  const [zoomMode, setZoomMode] = useState<ZoomMode>(FIT_WIDTH);
  const [zoom, setZoom] = useState(1);
  const [available, setAvailable] = useState({ height: 0, width: 0 });
  const scrollerRef = useRef<HTMLDivElement>(null);
  const zoomInputRef = useRef<HTMLInputElement>(null);
  /**
   * 输入框里正在编辑的文本。
   *
   * `type="number"` 的受控输入不能边打字边收敛：想输入 150，敲第一个字符就变成 50%，
   * 第二个字符再被并进去就成了 505%，用户根本打不出目标值。所以打字只动这份草稿，
   * 提交（回车 / 失焦 / 方向键）才落成 zoom。
   */
  const [zoomDraft, setZoomDraft] = useState<string | null>(null);

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

  /**
   * 量"能画多大"。
   *
   * 依赖里有 `pdf`，因为滚动容器是**加载完成后才渲染**的：只挂一次（`[]`）的话，挂载
   * 那一刻 `scrollerRef.current` 还是 null，这里直接 return，观察者再也不会被装上 ——
   * `available` 永远是 0，"适合宽度"于是永远算不出来，页面停在 100%（实测：真浏览器里
   * 观察者回调数为 0，按钮点了没反应）。
   *
   * 量父元素而不是滚动容器自己：滚动容器的 `clientWidth` 会被内容撑大（页面放大到超过
   * 视口时它跟着变宽），于是"适合宽度"会按被撑大的宽度去算，页面反而越缩越窄。父元素的
   * 尺寸由工作区决定，与页面无关。
   */
  useEffect(() => {
    const node = scrollerRef.current?.parentElement;
    if (!node) return;
    const measure = () =>
      setAvailable((previous) => {
        const next = { height: node.clientHeight, width: node.clientWidth };
        // 每次都造新对象会让下面依赖 `available` 的 useMemo 每帧都重算，
        // 于是每次测量都触发一次重渲染 —— 只在真的变了时才换引用。
        return previous.height === next.height && previous.width === next.width ? previous : next;
      });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [pdf]);

  const page = pageSize ?? FALLBACK_PAGE;
  const scale = useMemo(() => {
    if (zoomMode === FREE_ZOOM) return clamp(zoom);
    // 两种"适合"共用同一段：先按宽度定一个上限，适合页面再把高度这个上限并进来 ——
    // 谁更紧谁说了算，取小的那个才不会溢出可视区。
    const widthFit = (available.width - PAGE_GUTTER * 2) / page.width;
    const heightFit = (available.height - PAGE_GUTTER_VERTICAL) / page.height;
    const candidates = zoomMode === FIT_PAGE ? [widthFit, heightFit] : [widthFit];
    if (!candidates.every((candidate) => candidate > 0)) return clamp(zoom);
    return clamp(Math.min(...candidates));
  }, [available, page.height, page.width, zoom, zoomMode]);

  const scaled = useMemo(
    () => ({ width: page.width * scale, height: page.height * scale }),
    [page.width, page.height, scale],
  );

  /** 页宽是逐帧算出来的（适合档位下随窗口变），档位判断用整百分比。 */
  const percent = Math.round(scale * 100);

  const selectValue =
    zoomMode !== FREE_ZOOM
      ? zoomMode
      : (ZOOM_PERCENT_PRESETS as readonly number[]).includes(percent)
        ? String(percent)
        : CUSTOM_VALUE;

  // 缩放值变了就把草稿拉回真实值，但正在打字的那一格不动 —— 否则用户敲到一半就被重写。
  useEffect(() => {
    if (document.activeElement === zoomInputRef.current) return;
    setZoomDraft(String(percent));
  }, [percent]);

  const commitZoomPercent = useCallback((raw: number) => {
    if (!Number.isFinite(raw)) return;
    setZoomMode(FREE_ZOOM);
    setZoom(clamp(raw / 100));
  }, []);

  /** 档位按钮是开关：已经在适合宽度了，再点一次就交还手动缩放。 */
  const toggleZoomMode = (mode: typeof FIT_WIDTH | typeof FIT_PAGE) =>
    setZoomMode((current) => (current === mode ? FREE_ZOOM : mode));

  const handleZoomPreset = (value: string) => {
    if (value === FIT_WIDTH || value === FIT_PAGE) {
      setZoomMode(value);
      return;
    }
    // 「自定义」不是一个档位，只是输入框的入口：选中它等于邀请用户去打字。
    if (value === CUSTOM_VALUE) {
      zoomInputRef.current?.focus();
      return;
    }
    commitZoomPercent(Number(value));
  };

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

  // 载入完成后滚到目标页。只做一次：之后用户自己滚动，再把视口拽回去就成了骚扰。
  const locatedRef = useRef(false);
  useEffect(() => {
    if (!pdf || locatedRef.current) return;
    const target = locate?.page;
    if (!target || target <= 1) return;
    locatedRef.current = true;
    // 等一帧，让各页占位 div 先按真实尺寸布局出来 —— 占位尺寸是用第 1 页量的，
    // 在那之前 `offsetTop` 算出来是错的。
    const handle = window.requestAnimationFrame(() => goToPage(target));
    return () => window.cancelAnimationFrame(handle);
  }, [pdf, locate?.page, goToPage]);

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
          {/* 页数是一个整体：输入框、" / " 和总页数必须在同一行、同一条基线上。
              分开摆的时候，输入框被全局 input 规则拉成整行宽，"/ 20" 就被挤到框沿上了。 */}
          <span className="pdf-page-counter">
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
          </span>
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
            disabled={zoomMode === FREE_ZOOM && zoom <= MIN_SCALE}
            onClick={() => {
              setZoomMode(FREE_ZOOM);
              setZoom(clamp(scale / ZOOM_STEP));
            }}
            title="缩小"
            type="button"
          >
            <Minus aria-hidden="true" size={16} />
          </button>
          {/* 预设档位与手动输入是两个控件而不是一个：下拉按「选择」的语义报出当前档位
              （含两个"适合"档），输入框按「数字」的语义接受任意百分比。合成一个可输入的
              select 在键盘与读屏下的行为在各平台并不一致。 */}
          <select
            aria-label="缩放档位"
            className="pdf-zoom-select"
            onChange={(event) => handleZoomPreset(event.target.value)}
            value={selectValue}
          >
            <option value={FIT_WIDTH}>适合宽度</option>
            <option value={FIT_PAGE}>适合页面</option>
            {ZOOM_PERCENT_PRESETS.map((preset) => (
              <option key={preset} value={String(preset)}>
                {preset}%
              </option>
            ))}
            {/* 「自定义」只在当前值不落在任何档位上时出现：它不是一个可选中的档位，
                只是一个说明「下面输入框里的数字是手打的」的状态。 */}
            {selectValue === CUSTOM_VALUE ? <option value={CUSTOM_VALUE}>自定义</option> : null}
          </select>
          <span className="pdf-zoom-input-group">
            <input
              aria-label="缩放百分比"
              className="pdf-zoom-input"
              max={MAX_SCALE * 100}
              min={MIN_SCALE * 100}
              onBlur={() => {
                // 只把草稿落成真值；值没变时不写 store，免得重排一次页面。
                if (zoomDraft !== null) commitZoomPercent(Number.parseInt(zoomDraft, 10));
                setZoomDraft(String(percent));
              }}
              onChange={(event) => setZoomDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  commitZoomPercent(Number.parseInt(zoomDraft ?? "", 10));
                  setZoomDraft(null);
                } else if (event.key === "Escape") {
                  setZoomDraft(null);
                }
              }}
              ref={zoomInputRef}
              step={ZOOM_PERCENT_STEP}
              type="number"
              value={zoomDraft ?? String(percent)}
            />
            <span aria-hidden="true">%</span>
          </span>
          <button
            aria-label="放大"
            className="tree-icon-button"
            disabled={zoomMode === FREE_ZOOM && zoom >= MAX_SCALE}
            onClick={() => {
              setZoomMode(FREE_ZOOM);
              setZoom(clamp(scale * ZOOM_STEP));
            }}
            title="放大"
            type="button"
          >
            <Plus aria-hidden="true" size={16} />
          </button>
          {/* 两个"适合"各占一个按钮。它们是开关而不是单选项：再点一次当前档位就回到手动
              缩放，与「档位属于缩放入口的一部分」这套心智一致。 */}
          <button
            aria-pressed={zoomMode === FIT_WIDTH}
            className="button button-secondary pdf-fit-button"
            onClick={() => toggleZoomMode(FIT_WIDTH)}
            type="button"
          >
            适合宽度
          </button>
          <button
            aria-pressed={zoomMode === FIT_PAGE}
            className="button button-secondary pdf-fit-button"
            onClick={() => toggleZoomMode(FIT_PAGE)}
            type="button"
          >
            适合页面
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
            <PdfPageCanvas
              highlight={locate?.page === index + 1 ? (locate?.bbox ?? null) : null}
              pageNumber={index + 1}
              pdf={pdf}
              scale={scale}
              width={scaled.width}
            />
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
  highlight,
}: {
  pdf: PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  width: number;
  /** 要在这一页上框出来的矩形（PDF 点、原点左上），没有就为 null。 */
  highlight: [number, number, number, number] | null;
}) {
  const holderRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);
  const [box, setBox] = useState<{
    left: number;
    top: number;
    width: number;
    height: number;
  } | null>(null);

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
        if (highlight) {
          // 换算交给 pdf.js 的 viewport：它已经处理了缩放与 /Rotate 旋转，自己算矩阵
          // 在旋转页面上必然错。返回值是 [x0, y0, x1, y1]，原点左上、单位 CSS 像素。
          const [x0, y0, x1, y1] = viewport.convertToViewportRectangle(highlight);
          setBox({
            left: Math.min(x0, x1),
            top: Math.min(y0, y1),
            width: Math.abs(x1 - x0),
            height: Math.abs(y1 - y0),
          });
        } else {
          setBox(null);
        }
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
  }, [pdf, pageNumber, scale, visible, highlight]);

  return (
    <div className="pdf-page-canvas" ref={holderRef} style={{ width }}>
      {failed ? <p className="pdf-page-failed">第 {pageNumber} 页渲染失败</p> : null}
      <canvas hidden={failed} ref={canvasRef} />
      {box && !failed ? (
        // 描边而不是填充：填充会挡住图本身，而用户来这里正是为了看图。
        // pointer-events: none 让这一层不拦截滚动与选择。
        <span
          aria-hidden="true"
          className="pdf-page-highlight"
          style={{
            height: `${box.height}px`,
            left: `${box.left}px`,
            top: `${box.top}px`,
            width: `${box.width}px`,
          }}
        />
      ) : null}
    </div>
  );
}
