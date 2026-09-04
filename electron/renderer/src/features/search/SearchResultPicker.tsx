import { useState } from "react";
import { useCreateSearchImportMutation, useSearchRunQuery } from "./search.queries";

export function SearchResultPicker({ runId, sessionId, repositoryId, onCreated }: { runId: string; sessionId: string; repositoryId: string; onCreated(batchId: string): void }) {
  const run = useSearchRunQuery(runId, sessionId); const create = useCreateSearchImportMutation(runId); const [selected, setSelected] = useState<string[]>([]);
  if (!run.data) return <p>正在读取搜索结果…</p>;
  return <section aria-label="搜索结果"><ul>{run.data.results.map((result) => <li key={result.id}><label><input checked={selected.includes(result.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, result.id] : current.filter((id) => id !== result.id))} type="checkbox" />{result.title}</label><p>{result.snippet}</p></li>)}</ul><button className="button button-primary" disabled={!selected.length || create.isPending} onClick={() => void create.mutateAsync({ repositoryId, resultIds: selected }).then((batch) => onCreated(batch.id))} type="button">加入导入批次</button></section>;
}
