import { useQuery } from "@tanstack/react-query";

export const importKeys = {
  job: (jobId: string) => ["imports", jobId] as const,
  formats: ["imports", "formats"] as const,
};
export function useImportJobQuery(jobId: string | null) {
  return useQuery({
    queryKey: jobId ? importKeys.job(jobId) : ["imports", "job"],
    queryFn: () => window.docmind.imports.get(jobId!),
    enabled: jobId !== null,
  });
}

/**
 * The formats the backend can import as a single file.
 *
 * The source step renders one choice per entry, so the list of what a user can
 * pick follows from the backend — including formats a plugin contributes — and
 * never has to be restated here.
 */
export function useSourceFormatsQuery() {
  return useQuery({
    queryKey: importKeys.formats,
    queryFn: () => window.docmind.sources.listFormats(),
  });
}
