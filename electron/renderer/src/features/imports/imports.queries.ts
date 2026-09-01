import { useQuery } from "@tanstack/react-query";

export const importKeys = { job: (jobId: string) => ["imports", jobId] as const };
export function useImportJobQuery(jobId: string | null) {
  return useQuery({
    queryKey: jobId ? importKeys.job(jobId) : ["imports", "job"],
    queryFn: () => window.docmind.imports.get(jobId!),
    enabled: jobId !== null,
  });
}
