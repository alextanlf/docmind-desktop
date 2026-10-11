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
    // 抓取在后台跑，进度只在服务端。有来源在抓时轮询 —— 这比再造一条进度推送通道
    // 便宜得多，而抓取本来就只有"几分钟、每页一次"的粒度。没有在抓时完全不轮询。
    refetchInterval: (query) =>
      query.state.data?.sources.some((source) => source.crawlState === "running") ? 1000 : false,
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

export function useCrawlSourceMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (domain: string) => window.docmind.corpus.crawlSource(domain),
    onSuccess: (data) => {
      // 只把清单写回缓存；`summary` 是这一次的结果，由调用方展示，不该混进列表数据。
      if (data) client.setQueryData(corpusKeys.sources, { sources: data.sources });
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
