import { Check, ChevronDown, Database } from "lucide-react";
import { useEffect, useMemo } from "react";
import type { Repository } from "../../../../shared/contracts";

type RepositoryScopeProps = {
  onChange: (repositoryIds: string[]) => void;
  repositories: Repository[];
  selectedRepositoryIds: string[];
};

export function RepositoryScope({
  onChange,
  repositories,
  selectedRepositoryIds,
}: RepositoryScopeProps) {
  // 🔴 必须 memo：`indexed` 进了下方 effect 的依赖数组，而 filter 每次渲染
  // 都返回新数组 —— 不 memo 就等于依赖「每次都变」，effect 每帧都跑。
  // 目前靠 `valid.length !== selectedRepositoryIds.length` 侥幸不发 onChange，
  // 但只要这个守卫有任何变化（或 onChange 换成非稳定引用）就是死循环。
  const indexed = useMemo(
    () => repositories.filter((repository) => repository.indexedDocumentCount > 0),
    [repositories],
  );
  useEffect(() => {
    const valid = selectedRepositoryIds.filter((id) => indexed.some((repo) => repo.id === id));
    if (valid.length !== selectedRepositoryIds.length) onChange(valid);
  }, [indexed, onChange, selectedRepositoryIds]);
  const selected = indexed.filter((repository) => selectedRepositoryIds.includes(repository.id));
  const label = selected.length
    ? selected.map((repository) => repository.name).join("、")
    : "选择知识库";

  return (
    <details className="repository-scope">
      <summary aria-label="选择知识库" title="选择知识库">
        <Database aria-hidden="true" size={16} />
        <span>{label}</span>
        <ChevronDown aria-hidden="true" size={15} />
      </summary>
      <div className="repository-scope-menu" role="group" aria-label="知识库范围">
        {indexed.length === 0 ? <p>暂无已建立索引的知识库</p> : null}
        {indexed.map((repository) => {
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
