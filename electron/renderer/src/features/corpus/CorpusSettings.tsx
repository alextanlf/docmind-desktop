import { Trash2, X } from "lucide-react";
import { useRef, useState } from "react";
import type { CorpusCandidate, WebSource } from "../../../../shared/contracts";
import { IconButton } from "../../components/IconButton";
import { Modal } from "../../components/Modal";
import { REJECTION_COPY } from "../imports/rejection-copy";
import { clientErrorMessage } from "../settings/settings.queries";
import {
  useCancelCrawlMutation,
  useCrawlSourceMutation,
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
  const crawlSource = useCrawlSourceMutation();
  const cancelCrawl = useCancelCrawlMutation();
  const [topic, setTopic] = useState("");
  const [manual, setManual] = useState("");
  const [error, setError] = useState<string | null>(null);
  // 待确认移除的来源。移除会连带删掉语料，所以必须先把"删多少"讲清楚再动手。
  const [removing, setRemoving] = useState<WebSource | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

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

  async function cancel(domain: string) {
    setError(null);
    try {
      await cancelCrawl.mutateAsync(domain);
    } catch (cancelError) {
      setError(clientErrorMessage(cancelError));
    }
  }

  async function crawl(domain: string) {
    setError(null);
    try {
      // 抓取跑在后台：这里只需要知道"起没起来"，进度与结果从来源清单里读。
      await crawlSource.mutateAsync(domain);
    } catch (crawlError) {
      setError(clientErrorMessage(crawlError));
    }
  }

  /**
   * 一个来源的抓取状态。四种情况都要说清楚，尤其是"抓了但一页都没进来"和"没跑完"。
   *
   * 状态全部来自服务端：进度、结果、失败原因都落库，所以刷新页面或换个窗口看到的是
   * 同一件事 —— 而不是一个只活在这次渲染里的提示。
   */
  function status(source: WebSource) {
    if (source.crawlState === "running") {
      const pages = source.progress?.pages ?? 0;
      const budget = source.progress?.budget ?? 0;
      return budget > 0 ? `正在抓取：已抓 ${pages} 页（预算 ${budget}）` : "正在抓取…";
    }
    if (source.crawlState === "interrupted") {
      return "上次抓取没有跑完（应用退出或内部出错），可以重新抓取";
    }
    if (source.lastError) {
      const copy = REJECTION_COPY[source.lastError];
      return `上次抓取没有收录任何页面：${copy.label} —— ${copy.hint}`;
    }
    if (source.pageCount > 0) {
      const when = source.lastCrawledAt ? new Date(source.lastCrawledAt).toLocaleString() : "—";
      return `已收录 ${source.pageCount} 页 / ${source.chunkCount} 段，最近抓取 ${when}`;
    }
    return "还没有抓取";
  }

  /** 上一次抓完的结果。增量刷新的收益（未变几页）只有说出来才算数。 */
  function outcome(source: WebSource) {
    const summary = source.lastSummary;
    if (!summary || source.crawlState === "running") return null;
    const parts = [`更新 ${summary.updatedPages} 页`, `未变 ${summary.keptPages} 页`];
    if (summary.removedPages > 0) parts.push(`清理 ${summary.removedPages} 页已消失的页面`);
    parts.push(`跳过 ${summary.skippedCount} 个地址`);
    return `上次抓取：${parts.join("、")}；当前共 ${summary.pages} 页 / ${summary.chunks} 段${
      summary.truncated ? "（已达到页数上限，这不是全部）" : ""
    }。`;
  }

  async function remove(domain: string) {
    setError(null);
    try {
      await removeSource.mutateAsync(domain);
      setRemoving(null);
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
          {source.crawlState === "running" ? (
            // 运行中给的是"停止"而不是一个禁用的"抓取"：一个 200 页的抓取在慢站点上
            // 要好几分钟，用户必须能停，而不是只能等或者关掉应用。
            <button
              aria-label={`停止抓取 ${source.domain}`}
              className="button button-danger-quiet"
              disabled={cancelCrawl.isPending}
              onClick={() => void cancel(source.domain)}
              type="button"
            >
              停止
            </button>
          ) : (
            <button
              aria-label={`抓取 ${source.domain}`}
              className="button button-secondary"
              disabled={crawlSource.isPending}
              onClick={() => void crawl(source.domain)}
              type="button"
            >
              抓取
            </button>
          )}
          <button
            aria-label={`移除 ${source.domain}`}
            className="button button-danger-quiet"
            disabled={removeSource.isPending}
            onClick={() => setRemoving(source)}
            type="button"
          >
            移除
          </button>
        </div>
        <p
          className={
            source.lastError || source.crawlState === "interrupted"
              ? "corpus-status is-error"
              : "corpus-status"
          }
        >
          {status(source)}
        </p>
        {outcome(source) ? <p className="corpus-status">{outcome(source)}</p> : null}
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
        <p className="corpus-note">
          抓取会遵守 robots 与站点声明的间隔，只收录入口路径之下的页面，并受页数上限约束。
        </p>
      </section>

      {removing ? (
        <Modal
          backdropClassName="dialog-backdrop-nested"
          className="confirm-dialog"
          initialFocusRef={closeButtonRef}
          labelledBy="corpus-remove-title"
          onEscape={() => setRemoving(null)}
        >
          <div className="dialog-title-row">
            <h3 id="corpus-remove-title">确认移除来源</h3>
            <IconButton
              icon={<X aria-hidden="true" size={18} />}
              label="关闭确认窗口"
              onClick={() => setRemoving(null)}
              ref={closeButtonRef}
              size="small"
            />
          </div>
          {/* 把代价说清楚：移除不只是从清单上划掉一行，它还会删掉已经抓下来的语料。 */}
          <p>
            将从清单移除 <code>{removing.domain}</code>
            {removing.pageCount > 0
              ? `，并删除已抓取的 ${removing.pageCount} 页 / ${removing.chunkCount} 段语料`
              : "（它还没有抓取到任何语料）"}
            。删除不可恢复，之后要重新抓取。
          </p>
          <div className="dialog-actions">
            <button
              className="button button-secondary"
              onClick={() => setRemoving(null)}
              type="button"
            >
              取消
            </button>
            <button
              className="button button-danger"
              disabled={removeSource.isPending}
              onClick={() => void remove(removing.domain)}
              type="button"
            >
              <Trash2 aria-hidden="true" size={16} />
              确认移除
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
