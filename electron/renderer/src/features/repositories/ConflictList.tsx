import { useConflictsQuery, useResolveConflictMutation } from "./repository.queries";

export function ConflictList({ repositoryId }: { repositoryId: string }) {
  const conflicts = useConflictsQuery(repositoryId);
  const resolve = useResolveConflictMutation(repositoryId);

  if (conflicts.isPending || !conflicts.data || conflicts.data.length === 0) return null;

  return (
    <ul className="conflict-list">
      {conflicts.data.map((conflict) => (
        <li key={conflict.documentId} className="conflict-item">
          <span className="conflict-title">{conflict.title}</span>
          <div className="conflict-actions">
            <button
              onClick={() =>
                resolve.mutate({ documentId: conflict.documentId, resolution: "keep_local" })
              }
              type="button"
            >
              保留本地
            </button>
            <button
              onClick={() =>
                resolve.mutate({ documentId: conflict.documentId, resolution: "keep_remote" })
              }
              type="button"
            >
              保留远端
            </button>
            <button
              onClick={() =>
                resolve.mutate({ documentId: conflict.documentId, resolution: "keep_both" })
              }
              type="button"
            >
              两者都保留
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
