import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CorpusCandidateRequest, SaveWebSourcesRequest } from "../../../../shared/contracts";

/**
 * 网页语料的查询与写入。
 *
 * 收录 / 移除的响应都是**删除或写入之后的完整清单**，所以这里直接把响应写回缓存，
 * 而不是"改完再查一次"——两次请求之间列表会短暂显示旧状态，而用户刚点的那一下
 * 看起来就像没生效。
 */
export const corpusKeys = {
  sources: ["corpus", "sources"] as const,
};

export function useWebSourcesQuery() {
  return useQuery({
    queryKey: corpusKeys.sources,
    queryFn: () => window.docmind.corpus.sources(),
  });
}

export function useDeriveCandidatesMutation() {
  return useMutation({
    mutationFn: (input: CorpusCandidateRequest) => window.docmind.corpus.candidates(input),
  });
}

export function useSaveWebSourcesMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveWebSourcesRequest) => window.docmind.corpus.saveSources(input),
    onSuccess: (data) => {
      if (data) client.setQueryData(corpusKeys.sources, data);
    },
  });
}

export function useRemoveWebSourceMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (domain: string) => window.docmind.corpus.removeSource(domain),
    onSuccess: (data) => {
      if (data) client.setQueryData(corpusKeys.sources, data);
    },
  });
}
