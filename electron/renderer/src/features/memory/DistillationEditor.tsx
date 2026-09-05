import { RefreshCw, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DistillationTarget } from "../../../../shared/contracts";
import { useRepositoriesQuery } from "../repositories/repository.queries";
import { useDistillationMutations, useDistillationQuery } from "./memory.queries";

export function DistillationEditor({ distillationId }: { distillationId: string }) {
  const query = useDistillationQuery(distillationId);
  const { refetch } = query;
  const actions = useDistillationMutations(distillationId);
  const repositories = useRepositoriesQuery();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [keyPoints, setKeyPoints] = useState<string[]>([]);
  const [target, setTarget] = useState<"local" | "yuque" | null>(null);
  const [repositoryId, setRepositoryId] = useState("");
  const updateQueue = useRef<Promise<unknown>>(Promise.resolve());
  const lastSequence = useRef(0);
  useEffect(() => {
    if (query.data) {
      setTitle(query.data.title);
      setContent(query.data.content);
      setKeyPoints(query.data.keyPoints);
    }
  }, [query.data]);
  useEffect(() => {
    if (!query.data || !["generating", "saving"].includes(query.data.state)) return;
    return window.docmind.memory.subscribeDistillation(
      distillationId,
      lastSequence.current,
      (event) => {
        lastSequence.current = Math.max(lastSequence.current, event.sequence);
        if (event.type === "progress" || event.type === "done" || event.type === "error")
          void refetch();
      },
    ).detach;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch is stable for this query instance
  }, [distillationId, query.data?.state, refetch]);
  if (query.isPending) return <p>正在读取蒸馏草稿…</p>;
  if (!query.data) return <p role="alert">无法读取蒸馏草稿。</p>;
  const update = () => {
    const edit = { title, content, keyPoints };
    updateQueue.current = actions.update.mutateAsync(edit);
    return updateQueue.current;
  };
  const save = async () => {
    if (!target) return;
    const input: DistillationTarget = target === "local" ? { target } : { target, repositoryId };
    await updateQueue.current;
    await actions.save.mutateAsync(input);
  };
  const regenerate = async () => {
    await updateQueue.current;
    await actions.regenerate.mutateAsync();
  };
  return (
    <section className="distillation-editor" aria-label="蒸馏编辑器">
      <div className="memory-editor-toolbar">
        <label>
          标题
          <input
            aria-label="蒸馏标题"
            value={title}
            onBlur={update}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <button
          className="button button-secondary"
          disabled={
            actions.regenerate.isPending || actions.update.isPending || actions.save.isPending
          }
          onClick={() => void regenerate()}
          type="button"
        >
          <RefreshCw aria-hidden="true" size={16} />
          重新生成
        </button>
      </div>
      <label>
        蒸馏正文
        <textarea
          aria-label="蒸馏正文"
          value={content}
          onBlur={update}
          onChange={(event) => setContent(event.target.value)}
        />
      </label>
      <label>
        关键要点
        <textarea
          aria-label="关键要点"
          value={keyPoints.join("\n")}
          onBlur={update}
          onChange={(event) => setKeyPoints(event.target.value.split("\n").filter(Boolean))}
        />
      </label>
      <section aria-label="保存目标" className="memory-target">
        <label>
          <input
            checked={target === "local"}
            name="target"
            onChange={() => setTarget("local")}
            type="radio"
          />
          仅本地
        </label>
        <label>
          <input
            checked={target === "yuque"}
            name="target"
            onChange={() => setTarget("yuque")}
            type="radio"
          />
          语雀
        </label>
        {target === "yuque" ? (
          <select
            aria-label="语雀知识库"
            onChange={(event) => setRepositoryId(event.target.value)}
            value={repositoryId}
          >
            <option value="">选择知识库</option>
            {repositories.data
              ?.filter((item) => item.yuqueId)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        ) : null}
      </section>
      <div className="memory-save-row">
        <span role="status">
          {query.data.state === "saved_unindexed"
            ? "已保存，等待索引"
            : query.data.state === "saved"
              ? "已保存"
              : query.data.state === "failed"
                ? "操作失败，可重试"
                : "草稿"}
        </span>
        <button
          className="button button-primary"
          disabled={
            !target ||
            (target === "yuque" && !repositoryId) ||
            actions.save.isPending
          }
          onClick={() => void save()}
          type="button"
        >
          <Save aria-hidden="true" size={16} />
          保存知识
        </button>
      </div>
    </section>
  );
}
