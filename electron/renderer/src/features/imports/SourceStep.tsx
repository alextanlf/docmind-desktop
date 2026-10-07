import { FileText, Link, Upload } from "lucide-react";
import { useState } from "react";
import type { SourceRef } from "../../../../shared/contracts";
import { useSourceFormatsQuery } from "./imports.queries";
import { clientErrorMessage } from "../settings/settings.queries";

type SourceStepProps = { onContinue: (source: SourceRef) => Promise<void> };

/**
 * Choosing where a document comes from.
 *
 * The file choices are not a list held here: they are the backend's registered
 * formats, fetched on mount. Adding a format — including one a plugin
 * contributes — adds a choice here with no edit to this file. "网页 URL" is
 * the one fixed entry, because a URL is not a format: it is resolved by the
 * backend after the fact.
 */
export function SourceStep({ onContinue }: SourceStepProps) {
  const formats = useSourceFormatsQuery();
  const [selected, setSelected] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [source, setSource] = useState<SourceRef | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const available = formats.data ?? [];
  // Default to the URL tab until the format list arrives, then to the first
  // format — so the control is never empty while the request is in flight.
  const active = selected ?? (available[0]?.name ?? null);
  const activeLabel =
    available.find((format) => format.name === active)?.label ?? active ?? "";

  async function choose() {
    if (active === null) return;
    setError("");
    try {
      const staged = await window.docmind.dialogs.chooseSource(active);
      if (staged) {
        setSource({ kind: "staged_file", value: staged.stagedSourceId });
        setDisplayName(staged.name);
      }
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }

  async function continueToPreview() {
    const current = active === null ? { kind: "url" as const, value: url.trim() } : source;
    if (!current) {
      setError("请选择要导入的文件");
      return;
    }
    if (current.kind === "url") {
      try {
        const parsed = new URL(current.value);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new Error();
      } catch {
        setError("请输入有效的 HTTP(S) 网页地址");
        return;
      }
    }
    setPending(true);
    setError("");
    try {
      await onContinue(current);
    } catch (cause) {
      setError(clientErrorMessage(cause));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="import-step">
      <div aria-label="导入来源类型" className="segmented-control">
        <button
          aria-pressed={active === null}
          className={active === null ? "is-active" : ""}
          onClick={() => {
            setSelected(null);
            setError("");
          }}
          type="button"
        >
          网页 URL
        </button>
        {available.map((format) => (
          <button
            aria-pressed={active === format.name}
            className={active === format.name ? "is-active" : ""}
            key={format.name}
            onClick={() => {
              setSelected(format.name);
              setError("");
            }}
            type="button"
          >
            {format.label}
          </button>
        ))}
      </div>
      {active === null ? (
        <label className="dialog-field">
          <span>网页 URL</span>
          <input
            aria-label="网页 URL"
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com/article"
            value={url}
          />
        </label>
      ) : (
        <div className="source-choice">
          <button className="button button-secondary" onClick={() => void choose()} type="button">
            <Upload aria-hidden="true" size={16} />
            选择 {activeLabel} 文件
          </button>
          {displayName ? (
            <p>
              <FileText aria-hidden="true" size={15} />
              {displayName}
            </p>
          ) : null}
        </div>
      )}
      {formats.isError ? (
        <p className="inline-error" role="alert">
          可导入的文档格式加载失败：{clientErrorMessage(formats.error)}
        </p>
      ) : null}
      {error ? (
        <p className="editor-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="dialog-actions">
        <button
          className="button button-primary"
          disabled={pending}
          onClick={() => void continueToPreview()}
          type="button"
        >
          {pending ? "正在解析…" : "继续"}
          <Link aria-hidden="true" size={16} />
        </button>
      </div>
    </div>
  );
}
