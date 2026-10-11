import { useState } from "react";
import type { CorpusCandidate, WebSource } from "../../../../shared/contracts";
import { clientErrorMessage } from "../settings/settings.queries";
import {
  useDeriveCandidatesMutation,
  useRemoveWebSourceMutation,
  useSaveWebSourcesMutation,
  useWebSourcesQuery,
} from "./corpus.queries";
import { signalLabel } from "./signal-copy";

/**
 * 网页语料：决定本地网页索引收录哪些站点。
 *
 * 这一页的存在理由是一个具体的用户问题：**"为什么只抓到这么点东西？"** 所以每条候选
 * 都带着"谁提名的、凭什么"，并且把"我们核对过的种子表"与"模型的一家之言"分开显示 ——
 * 两者的可信度不同，混在一起等于把判断责任推给用户却又不给依据。
 *
 * 候选是**按需推导**的，不是自动跑的：它要调用模型，而每次进设置页都打一次模型调用
 * 既慢又花钱。用户点了"推导来源"才发生这件事。
 */
export function CorpusSettings() {
  const sources = useWebSourcesQuery();
  const derive = useDeriveCandidatesMutation();
  const saveSources = useSaveWebSourcesMutation();
  const removeSource = useRemoveWebSourceMutation();
  const [topic, setTopic] = useState("");
  const [manual, setManual] = useState("");
  const [error, setError] = useState<string | null>(null);

  const collected = sources.data?.sources ?? [];
  const known = new Set(collected.map((source) => source.domain));

  async function collect(source: {
    domain: string;
    entryUrl?: string | null;
    label?: string | null;
    kind?: string | null;
    verified: boolean;
    evidence: string[];
  }) {
    setError(null);
    try {
      await saveSources.mutateAsync({
        sources: [
          {
            domain: source.domain,
            entryUrl: source.entryUrl ?? undefined,
            label: source.label ?? undefined,
            kind: source.kind ?? undefined,
            verified: source.verified,
            evidence: source.evidence,
          },
        ],
      });
    } catch (collectError) {
      setError(clientErrorMessage(collectError));
    }
  }

  async function addManual() {
    const value = manual.trim();
    if (!value) return;
    await collect({ domain: value, verified: false, evidence: ["你手工添加"] });
    setManual("");
  }

  async function remove(domain: string) {
    setError(null);
    try {
      await removeSource.mutateAsync(domain);
    } catch (removeError) {
      setError(clientErrorMessage(removeError));
    }
  }

  function candidate(source: CorpusCandidate) {
    return (
      <li key={source.domain}>
        <div className="corpus-row">
          <code>{source.domain}</code>
          {source.label ? <span className="corpus-label">{source.label}</span> : null}
          <span className={source.verified ? "corpus-badge is-verified" : "corpus-badge"}>
            {source.verified ? "已核对来源" : "未经核对"}
          </span>
          <span className="corpus-signals">
            {source.signals.map((signal) => signalLabel(signal)).join(" · ")}
          </span>
          <button
            aria-label={`${known.has(source.domain) ? "已收录" : "收录"} ${source.domain}`}
            className="button button-secondary"
            disabled={known.has(source.domain) || saveSources.isPending}
            onClick={() => void collect(source)}
            type="button"
          >
            {known.has(source.domain) ? "已收录" : "收录"}
          </button>
        </div>
        <ul className="corpus-evidence">
          {source.evidence.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </li>
    );
  }

  function collectedSource(source: WebSource) {
    return (
      <li key={source.domain}>
        <div className="corpus-row">
          <code>{source.domain}</code>
          {source.label ? <span className="corpus-label">{source.label}</span> : null}
          <span className={source.verified ? "corpus-badge is-verified" : "corpus-badge"}>
            {source.verified ? "已核对来源" : "未经核对"}
          </span>
          {source.entryUrl ? <span className="corpus-entry">{source.entryUrl}</span> : null}
          <button
            aria-label={`移除 ${source.domain}`}
            className="button button-danger-quiet"
            disabled={removeSource.isPending}
            onClick={() => void remove(source.domain)}
            type="button"
          >
            移除
          </button>
        </div>
        <ul className="corpus-evidence">
          {source.evidence.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </li>
    );
  }

  return (
    <div className="corpus-settings">
      <section>
        <h3>推导候选来源</h3>
        <p className="corpus-note">
          写下你关心的问题或主题。种子表按关键词匹配第一方来源，模型再补充它的建议。
        </p>
        <label className="corpus-field">
          问题或主题
          <textarea
            aria-label="问题或主题"
            onChange={(event) => setTopic(event.target.value)}
            rows={2}
            value={topic}
          />
        </label>
        <button
          className="button button-primary"
          disabled={!topic.trim() || derive.isPending}
          onClick={() => derive.mutate({ text: topic, limit: 20 })}
          type="button"
        >
          {derive.isPending ? "推导中…" : "推导来源"}
        </button>
        {derive.isError ? (
          <p className="error-copy" role="alert">
            {clientErrorMessage(derive.error)}
          </p>
        ) : null}
        {derive.data ? (
          <>
            {derive.data.nomination.attempted && derive.data.nomination.count === 0 ? (
              <p className="corpus-note" role="status">
                模型这次没有给出建议（可能不可用，或输出无法解析）。以下候选来自种子表。
              </p>
            ) : null}
            {derive.data.candidates.length === 0 ? (
              <p className="corpus-note" role="status">
                没有推导出候选。可以在下面直接添加你知道的站点。
              </p>
            ) : (
              <ul className="corpus-list">{derive.data.candidates.map(candidate)}</ul>
            )}
          </>
        ) : null}
      </section>

      <section>
        <h3>已收录的来源（{collected.length}）</h3>
        <div className="corpus-manual">
          <label className="corpus-field">
            域名或网址
            <input
              aria-label="域名或网址"
              onChange={(event) => setManual(event.target.value)}
              placeholder="docs.example.com 或 https://docs.example.com/guide"
              value={manual}
            />
          </label>
          <button
            className="button button-secondary"
            disabled={!manual.trim() || saveSources.isPending}
            onClick={() => void addManual()}
            type="button"
          >
            添加
          </button>
        </div>
        {error ? (
          <p className="error-copy" role="alert">
            {error}
          </p>
        ) : null}
        {collected.length === 0 ? (
          <p className="corpus-note">还没有收录任何来源。</p>
        ) : (
          <ul className="corpus-list">{collected.map(collectedSource)}</ul>
        )}
        {/* 状态如实说明：这一步只是把范围定下来。 */}
        <p className="corpus-note">抓取与索引还没接入：现在只是把范围定下来。</p>
      </section>
    </div>
  );
}
