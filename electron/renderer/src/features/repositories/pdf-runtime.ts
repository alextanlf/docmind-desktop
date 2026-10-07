/**
 * pdf.js 运行时装配。
 *
 * 单独成一个叶子模块，是为了让 worker 的注册只发生一次，
 * 并且让 `PdfOriginalView` 只依赖这一处出口。
 *
 * worker 走 `?url` 导入：Vite 会把它作为独立资源产出并给出 URL。
 * 打包后页面从 `file://` 加载，而 electron-vite 的 `base` 是 `./`，
 * 因此这个 URL 是相对的，能被 `new Worker(url, { type: "module" })` 正确解析。
 * 这条路径必须在**打包态**实测（开发态走 http server，验不出问题）。
 */
import { GlobalWorkerOptions } from "pdfjs-dist";
import workerSource from "pdfjs-dist/build/pdf.worker.min.mjs?url";

/**
 * `?url` 在产物里给出的是**裸相对文件名**（如 `pdf.worker.min-<hash>.mjs`），
 * 而 `new Worker()` 按 **文档** URL 解析，不是按当前脚本 —— 直接赋值会指向
 * `out/renderer/` 而不是 `out/renderer/assets/`，worker 必然 404。
 * 用 `import.meta.url` 把基准钉回产出它的那个 chunk。
 */
GlobalWorkerOptions.workerSrc = new URL(workerSource, import.meta.url).href;

export { getDocument, PDFDataRangeTransport } from "pdfjs-dist";
export type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
