import { useVersionsQuery } from "./repository.queries";

export function VersionHistory({ documentId }: { documentId: string }) {
  const versions = useVersionsQuery(documentId);

  if (versions.isPending || !versions.data || versions.data.length === 0) return null;

  return (
    <ul className="version-history" aria-label="版本历史">
      {versions.data.map((version) => (
        <li key={version.versionNo}>
          <span>版本 {version.versionNo}</span>
          <time>{version.createdAt}</time>
        </li>
      ))}
    </ul>
  );
}
