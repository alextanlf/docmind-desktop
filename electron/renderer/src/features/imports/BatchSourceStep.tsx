import { FolderOpen } from "lucide-react";
import { useState } from "react";
import { clientErrorMessage } from "../settings/settings.queries";
import type { Repository } from "../../../../shared/contracts";

export function BatchSourceStep({ repositories, onCreated }: { repositories: Repository[]; onCreated: (batchId: string, discoveryVersion: number) => void }) {
  const [repositoryId, setRepositoryId] = useState(repositories[0]?.id ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function chooseDirectory() {
    const selectedRepositoryId = repositoryId || repositories[0]?.id;
    if (!selectedRepositoryId) { setError("请选择目标知识库"); return; }
    setPending(true); setError("");
    try {
      const staged = await window.docmind.sources.stageDirectory();
      if (!staged) return;
      const batch = await window.docmind.batches.create({ kind: "staged_directory", sourceId: staged.collectionId, repositoryId: selectedRepositoryId });
      onCreated(batch.id, batch.discoveryVersion);
    } catch (cause) { setError(clientErrorMessage(cause)); }
    finally { setPending(false); }
  }
  return <div className="import-step batch-source-step">
    <label className="dialog-field"><span>目标知识库</span><select aria-label="目标知识库" value={repositoryId} onChange={(e) => setRepositoryId(e.target.value)}><option value="">请选择</option>{repositories.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
    <button className="button button-secondary" disabled={pending} onClick={() => void chooseDirectory()} type="button"><FolderOpen aria-hidden="true" size={16} />{pending ? "正在读取…" : "选择目录"}</button>
    {error ? <p className="editor-error" role="alert">{error}</p> : null}
  </div>;
}
