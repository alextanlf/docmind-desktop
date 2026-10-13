import { Check, ChevronDown, Database } from "lucide-react";
import { useEffect, useMemo } from "react";
import type { Repository } from "../../../../shared/contracts";

type RepositoryScopeProps = {
  /** summary 的无障碍名。同一个页面上可能出现两个，用它区分是哪一处。 */
  ariaLabel?: string;
  /** 一个知识库都没有时的提示。 */
  emptyText?: string;
  /**
   * 默认只列已建立索引的知识库：会话的检索范围由索引决定，没索引的选了也没意义。
   * 记忆页要关掉它 —— 会话摘要和知识蒸馏挂在知识库上，跟索引建没建完无关。
   */
  indexedOnly?: boolean;
  onChange: (repositoryIds: string[]) => void;
  repositories: Repository[];
  selectedRepositoryIds: string[];
};

export function RepositoryScope({
  ariaLabel = "选择知识库",
  emptyText = "暂无已建立索引的知识库",
  indexedOnly = true,
  onChange,
  repositories,
  selectedRepositoryIds,
}: RepositoryScopeProps) {
  // 🔴 必须 memo：`selectable` 进了下方 effect 的依赖数组，而 filter 每次渲染
  // 都返回新数组 —— 不 memo 就等于依赖「每次都变」，effect 每帧都跑。
  // 目前靠 `valid.length !== selectedRepositoryIds.length` 侥幸不发 onChange，
  // 但只要这个守卫有任何变化（或 onChange 换成非稳定引用）就是死循环。
  const selectable = useMemo(
    () =>
      indexedOnly
        ? repositories.filter((repository) => repository.indexedDocumentCount > 0)
        : repositories,
    [indexedOnly, repositories],
  );
  useEffect(() => {
    const valid = selectedRepositoryIds.filter((id) => selectable.some((repo) => repo.id === id));
    if (valid.length !== selectedRepositoryIds.length) onChange(valid);
  }, [selectable, onChange, selectedRepositoryIds]);
  const selected = selectable.filter((repository) => selectedRepositoryIds.includes(repository.id));
  const label = selected.length
    ? selected.map((repository) => repository.name).join("、")
    : "选择知识库";

  return (
    <details className="repository-scope">
      <summary aria-label={ariaLabel} title={ariaLabel}>
        <Database aria-hidden="true" size={16} />
        <span>{label}</span>
        <ChevronDown aria-hidden="true" size={15} />
      </summary>
      <div className="repository-scope-menu" role="group" aria-label="知识库范围">
        {selectable.length === 0 ? <p>{emptyText}</p> : null}
        {selectable.map((repository) => {
          const checked = selectedRepositoryIds.includes(repository.id);
          return (
            <label key={repository.id}>
              <input
                checked={checked}
                onChange={() =>
                  onChange(
                    checked
                      ? selectedRepositoryIds.filter((id) => id !== repository.id)
                      : [...selectedRepositoryIds, repository.id],
                  )
                }
                type="checkbox"
              />
              <span>{repository.name}</span>
              {checked ? <Check aria-hidden="true" size={14} /> : null}
            </label>
          );
        })}
      </div>
    </details>
  );
}
