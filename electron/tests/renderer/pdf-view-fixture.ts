import { vi } from "vitest";
import { installDocMindApi } from "./test-docmind-api";

/**
 * PDF 阅读器用例的共用脚手架。
 *
 * 三个用例文件（适合宽度 / 适合页面 / 缩放档位）要用同一份"文档已加载 + 工作区尺寸已知"
 * 的前置状态。复制三份的下场是：改动阅读器的加载路径时只改了一份，另两份还绿着 —— 那份
 * 绿说明不了任何事。
 *
 * 注意 `getDocument` 的替换由各用例文件自己声明：`vi.mock` 是提升的，工厂里只能引用
 * `vi.hoisted` 出来的东西，没法从模块边界上收进来。这里只负责装配替身的行为。
 */

/** US Letter @72dpi：纵向比横向紧，能真正区分"适合宽度"与"适合页面"。 */
export const PAGE_WIDTH = 612;
export const PAGE_HEIGHT = 792;

/** 工作区给滚动容器的可用空间。默认宽 900 / 高 700。 */
export const AVAILABLE_WIDTH = 900;
export const AVAILABLE_HEIGHT = 700;

export type PdfFixture = {
  getDocument: ReturnType<typeof vi.fn>;
  /** 页面容器（`.pdf-original`）此刻报出来的可用尺寸。 */
  viewport: { height: number; width: number };
  /** 装上 window.docmind 的替身。 */
  installApi: (overrides?: Parameters<typeof installDocMindApi>[0]) => void;
  /** 把"注册时立刻回调"的观察者换成"只在显式通知时才回调"的版本。 */
  useManualResizeObservers: () => ManualResizeObservers;
  cleanup: () => void;
};

export type ManualResizeObservers = {
  /** 被观察的元素，按注册顺序。 */
  observed: Element[];
  /** 让所有观察者各报一次当前尺寸。 */
  notify: () => void;
};

type EnsurePdfReadyOptions = {
  getDocument: ReturnType<typeof vi.fn>;
  pageCount?: number;
  pageHeight?: number;
  pageWidth?: number;
};

/**
 * 让页尺寸、可用空间、ResizeObserver 都就位。
 *
 * 真实布局里 `.pdf-original` 的尺寸由工作区决定，与页面内容无关 —— 替身也必须这样：
 * 只对那个元素给出非零尺寸，其它元素一律 0。观察者用 setup.ts 装的那个（注册后一个
 * 微任务回调一次，与真浏览器首次 observe 就报一次当前尺寸一致），这里不重复替换。
 */
export function ensurePdfReady({
  getDocument,
  pageCount = 2,
  pageHeight = PAGE_HEIGHT,
  pageWidth = PAGE_WIDTH,
}: EnsurePdfReadyOptions): PdfFixture {
  const viewport = { height: AVAILABLE_HEIGHT, width: AVAILABLE_WIDTH };
  getDocument.mockReset();
  getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: pageCount,
      // 只实现 getPage(1).getViewport({scale:1}) 这条路径，其余方法阅读器不碰。
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({
          width: pageWidth * scale,
          height: pageHeight * scale,
        }),
      }),
      destroy: vi.fn(),
    }),
    destroy: vi.fn(),
  });

  const originalClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, "clientWidth");
  const originalClientHeight = Object.getOwnPropertyDescriptor(Element.prototype, "clientHeight");
  const originalResizeObserver = globalThis.ResizeObserver;
  // DOM 的属性名与 fixture 的字段名不一样（clientWidth ↔ width），这里显式配对：
  // 用 DOM 名去索引 viewport 会永远拿到 undefined，而 undefined 会让"适合"算成 100% ——
  // 看上去像"没生效"，实际是替身自己错了。
  const define = (domKey: "clientHeight" | "clientWidth", key: "height" | "width") =>
    Object.defineProperty(Element.prototype, domKey, {
      configurable: true,
      get(this: Element) {
        return this.classList.contains("pdf-original") ? viewport[key] : 0;
      },
    });
  define("clientWidth", "width");
  define("clientHeight", "height");

  return {
    getDocument,
    viewport,
    installApi: (overrides) => installDocMindApi(overrides),
    useManualResizeObservers: () => {
      const callbacks: Array<() => void> = [];
      const observed: Element[] = [];
      class ManualResizeObserver {
        constructor(private readonly callback: () => void) {
          callbacks.push(callback);
        }
        observe(element: Element) {
          observed.push(element);
        }
        unobserve() {}
        disconnect() {}
      }
      vi.stubGlobal("ResizeObserver", ManualResizeObserver);
      const notify = () => callbacks.forEach((callback) => callback());
      return { notify, observed };
    },
    cleanup: () => {
      if (originalClientWidth) {
        Object.defineProperty(Element.prototype, "clientWidth", originalClientWidth);
      } else {
        Reflect.deleteProperty(Element.prototype, "clientWidth");
      }
      if (originalClientHeight) {
        Object.defineProperty(Element.prototype, "clientHeight", originalClientHeight);
      } else {
        Reflect.deleteProperty(Element.prototype, "clientHeight");
      }
      // 只还原本 fixture 换过的那个全局：setup.ts 装的 ResizeObserver 必须交还回去，
      // 下一个用例（甚至下一个用例文件）还要靠它。unstubAllGlobals 会把所有全局一起
      // 打回原形，包括这里没动过的那些。
      vi.stubGlobal("ResizeObserver", originalResizeObserver);
    },
  };
}

/** 页面容器的渲染尺寸。缩放没真的生效时它不会变。 */
export function pageBox(container: HTMLElement): { height: number; width: number } {
  const wrapper = container.querySelector<HTMLElement>(".pdf-page");
  return {
    height: Number.parseFloat(wrapper?.style.height ?? "0"),
    width: Number.parseFloat(wrapper?.style.width ?? "0"),
  };
}
