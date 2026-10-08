import { useState } from "react";
import { useCreateSearchImportMutation, useSearchRunQuery } from "./search.queries";

export function SearchResultPicker({
  runId,
  sessionId,
  repositoryId,
  onCreated,
}: {
  runId: string;
  sessionId: string;
  repositoryId: string;
  onCreated(batchId: string): void;
}) {
  const run = useSearchRunQuery(runId, sessionId);
  const create = useCreateSearchImportMutation(runId);
  const [selected, setSelected] = useState<string[]>([]);
  if (!run.data) return <p>正在读取搜索结果…</p>;
  return (
    <section aria-label="搜索结果">
      <p>来源：{providerLabel(run.data.provider)}</p>
      <ul>
        {run.data.results.map((result) => (
          <li key={result.id}>
            <label>
              <input
                checked={selected.includes(result.id)}
                onChange={(event) =>
                  setSelected((current) =>
                    event.target.checked
                      ? [...current, result.id]
                      : current.filter((id) => id !== result.id),
                  )
                }
                type="checkbox"
              />
              {result.title}
            </label>
            <p>{result.snippet}</p>
          </li>
        ))}
      </ul>
      <button
        className="button button-primary"
        disabled={!selected.length || create.isPending}
        onClick={() =>
          void create
            .mutateAsync({ repositoryId, resultIds: selected })
            .then((batch) => onCreated(batch.id))
        }
        type="button"
      >
        加入导入批次
      </button>
    </section>
  );
}

function providerLabel(provider: string | null | undefined): string {
  if (!provider) return "联网搜索";
  if (provider.startsWith("model")) return "模型内置联网";
  if (provider === "tavily") return "Tavily";
  // 该来源的实现已删除，但历史 run 记录里仍有这个名字，保留标签以免旧记录显示成生字符串。
  if (provider === "duckduckgo") return "免费兜底（DuckDuckGo）";
  return "联网搜索";
}
