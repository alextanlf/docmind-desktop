import { useEffect, useState } from "react";
import type { DocumentFigure } from "../../../../shared/contracts";

type DocumentFiguresProps = {
  documentId: string;
  figures: DocumentFigure[];
  /**
   * 点击插图时的回调：把这张图在原件里的位置交出去（`bbox` 为 null 表示只跳页）。
   *
   * 传 `undefined` 表示这篇文档没有可定位的原件（Word/HTML 的图没有页面几何，远程文档
   * 没有本地 PDF），此时图不可点 —— 而不是点下去什么也不发生。
   */
  onLocate?: (target: { page: number; bbox: [number, number, number, number] | null }) => void;
};

/**
 * 文档里的插图。
 *
 * 图片字节经 IPC 拿（渲染层不持有后端地址与令牌），用 `Blob` + `URL.createObjectURL`
 * 渲染：`data:` URL 会把图片膨胀成 base64 常驻内存，一页几 MB 的扫描件会让整个文档视图
 * 卡住。
 *
 * 每张图都带页码。这不是装饰 —— 扫描课件里同一张状态转移图会在多页反复出现，用户需要
 * 知道当前看的是哪一页的那一张，也才知道去原件哪一页对照。
 */
export function DocumentFigures({ documentId, figures, onLocate }: DocumentFiguresProps) {
  if (figures.length === 0) return null;
  return (
    <section aria-label="文档插图" className="document-figures">
      <h2 className="document-figures-title">插图（{figures.length}）</h2>
      <ul className="document-figures-list">
        {figures.map((figure) => (
          <FigureItem
            documentId={documentId}
            figure={figure}
            key={`${figure.pageNumber ?? 0}:${figure.asset}`}
            onLocate={onLocate}
          />
        ))}
      </ul>
    </section>
  );
}

function FigureItem({
  documentId,
  figure,
  onLocate,
}: {
  documentId: string;
  figure: DocumentFigure;
  onLocate?: DocumentFiguresProps["onLocate"];
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let revoked: string | null = null;
    let disposed = false;
    void (async () => {
      try {
        const base64 = await window.docmind.documents.readAsset(documentId, figure.asset);
        if (disposed) return;
        const blob = base64ToBlob(base64, "image/png");
        revoked = URL.createObjectURL(blob);
        setUrl(revoked);
      } catch {
        // 单张图取不到不该让整个列表消失 —— 用户至少还能看见图的存在与页码。
        if (!disposed) setFailed(true);
      }
    })();
    return () => {
      disposed = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [documentId, figure.asset]);

  // 只有知道页码才能跳。整页扫描件也带页码，跳过去本身就是有用的。
  const locatable = onLocate !== undefined && figure.pageNumber !== null;
  const caption = figure.caption.trim();
  // 页码**始终**显示，不被图注取代：扫描课件里同一张图会在多页反复出现，只知道图注
  // 就不知道当前看的是哪一页那一张，也没法去原件对照。
  const position = figureLabel(figure);

  const locate = (): void => {
    if (!onLocate || figure.pageNumber === null) return;
    onLocate({
      page: figure.pageNumber,
      bbox: figure.bbox ? [figure.bbox[0], figure.bbox[1], figure.bbox[2], figure.bbox[3]] : null,
    });
  };

  // 图本身可点（最自然的入口：看到图就想看原件里的它），另外给一个显式按钮 ——
  // 键盘用户与"点了没反应就不知道能不能点"的人都靠它。
  const image = url ? (
    <img
      alt={caption || position}
      className="document-figure-image"
      height={figure.height || undefined}
      loading="lazy"
      src={url}
      width={figure.width || undefined}
    />
  ) : null;

  return (
    <li className="document-figure">
      {locatable && image ? (
        <button
          className="document-figure-image-button"
          onClick={locate}
          title={figure.bbox ? "在原件中定位这张图" : "在原件中打开这一页"}
          type="button"
        >
          {image}
        </button>
      ) : (
        image
      )}
      {!url ? (
        // 占位符同样要带上位置：只说「打不开」的话，用户不知道是哪一张。
        <p className="document-figure-placeholder" role="status">
          {failed ? "这张图暂时打不开" : "正在加载插图"} · {position}
        </p>
      ) : null}
      <p className="document-figure-caption">{position}</p>
      {caption ? <p className="document-figure-caption">{caption}</p> : null}
      {locatable && image ? (
        <button
          className="button button-secondary document-figure-locate"
          onClick={locate}
          type="button"
        >
          {figure.bbox ? "在原件中定位" : "在原件中打开这一页"}
        </button>
      ) : null}
    </li>
  );
}

function figureLabel(figure: DocumentFigure): string {
  const page = figure.pageNumber;
  if (page === null) return "插图";
  return figure.fullPage ? `第 ${page} 页（整页扫描）` : `第 ${page} 页插图`;
}

function base64ToBlob(encoded: string, mediaType: string): Blob {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mediaType });
}
