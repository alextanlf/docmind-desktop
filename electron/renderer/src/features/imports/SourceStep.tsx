import { FileText, Link, Upload } from "lucide-react";
import { useState } from "react";
import type { SourceRef } from "../../../../shared/contracts";
import { clientErrorMessage } from "../settings/settings.queries";

type SourceKind = "url" | "pdf" | "markdown";
type SourceStepProps = { onContinue: (source: SourceRef) => Promise<void> };
const labels: Record<SourceKind, string> = { url: "网页 URL", pdf: "PDF", markdown: "Markdown" };
export function SourceStep({ onContinue }: SourceStepProps) {
  const [kind, setKind] = useState<SourceKind>("url");
  const [url, setUrl] = useState("");
  const [source, setSource] = useState<SourceRef | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function choose() {
    if (kind === "url") return;
    setError("");
    try {
      const staged = await window.docmind.dialogs.chooseSource(kind);
      if (staged) {
        setSource({ kind: "staged_file", value: staged.stagedSourceId });
        setDisplayName(staged.name);
      }
    } catch (cause) {
      setError(clientErrorMessage(cause));
    }
  }
  async function continueToPreview() {
    const current = kind === "url" ? { kind: "url" as const, value: url.trim() } : source;
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
        {(Object.keys(labels) as SourceKind[]).map((value) => (
          <button
            aria-pressed={kind === value}
            className={kind === value ? "is-active" : ""}
            key={value}
            onClick={() => {
              setKind(value);
              setError("");
            }}
            type="button"
          >
            {labels[value]}
          </button>
        ))}
      </div>
      {kind === "url" ? (
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
            选择 {kind === "pdf" ? "PDF" : "Markdown"} 文件
          </button>
          {displayName ? (
            <p>
              <FileText aria-hidden="true" size={15} />
              {displayName}
            </p>
          ) : null}
        </div>
      )}
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
