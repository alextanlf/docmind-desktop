import { Check, ChevronDown, Database } from "lucide-react";
import { useEffect } from "react";
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
  const indexed = repositories.filter((repository) => repository.indexedDocumentCount > 0);
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
