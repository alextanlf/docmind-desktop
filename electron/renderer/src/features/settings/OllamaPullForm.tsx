import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ollamaKeys, clientErrorMessage, useOllamaPullQuery } from "./settings.queries";
import { TaskProgress } from "../../components/TaskProgress";

export function OllamaPullForm() {
  const [modelName, setModelName] = useState("");
  const [pullId, setPullId] = useState<string | null>(null);
  const client = useQueryClient();
  const pull = useMutation({ mutationFn: () => window.docmind.ollama.pull({ modelName: modelName.trim() }), onSuccess: (data) => { setModelName(""); setPullId(data.id); client.setQueryData(ollamaKeys.pull(data.id), data); } });
  const snapshot = useOllamaPullQuery(pullId);
  const cancel = useMutation({ mutationFn: () => window.docmind.ollama.cancelPull(pullId as string), onSuccess: (data) => client.setQueryData(ollamaKeys.pull(data.id), data) });
  const retry = useMutation({ mutationFn: () => window.docmind.ollama.retryPull(pullId as string), onSuccess: (data) => client.setQueryData(ollamaKeys.pull(data.id), data) });
  return <form onSubmit={(e) => { e.preventDefault(); if (modelName.trim()) pull.mutate(); }} aria-label="拉取 Ollama 模型">
    <label>拉取模型<input value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder="例如 llama3.2" /></label>
    <button type="submit" disabled={pull.isPending || !modelName.trim()}>拉取</button>
    {pull.isError && <p role="alert">{clientErrorMessage(pull.error)}</p>}
    {snapshot.data && <div><TaskProgress label="模型拉取进度" progress={snapshot.data.progress} status={snapshot.data.status ?? snapshot.data.state} />{["queued","running"].includes(snapshot.data.state) && <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>{cancel.isPending ? "正在取消…" : "取消"}</button>}{snapshot.data.state === "failed" && snapshot.data.retryable && <button type="button" onClick={() => retry.mutate()}>重试</button>}</div>}
  </form>;
}
