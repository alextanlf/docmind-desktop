import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RuntimeSettingsInput, WebSearchSettingsInput } from "../../../../shared/contracts";
import { clientErrorMessage, errorAction, isRetryable } from "./ollama-errors";

export { clientErrorMessage, errorAction, isRetryable } from "./ollama-errors";

export const settingsKeys = {
  root: ["settings"] as const,
  embedding: ["embedding", "status"] as const,
  yuque: ["yuque", "status"] as const,
  runtime: ["settings", "runtime"] as const,
};
export const ollamaKeys = {
  status: ["ollama", "status"] as const,
  models: ["ollama", "models"] as const,
  pull: (pullId: string) => ["ollama", "pull", pullId] as const,
};

export function useSettingsQuery() {
  return useQuery({ queryKey: settingsKeys.root, queryFn: () => window.docmind.settings.get() });
}

export function useEmbeddingStatusQuery() {
  return useQuery({
    queryKey: settingsKeys.embedding,
    queryFn: () => window.docmind.embedding.status(),
    refetchInterval: (query) => (query.state.data?.state === "downloading" ? 1_000 : false),
  });
}

export function useYuqueStatusQuery() {
  return useQuery({ queryKey: settingsKeys.yuque, queryFn: () => window.docmind.yuque.status() });
}

export function useRuntimeSettingsQuery(enabled = true) {
  return useQuery({ queryKey: settingsKeys.runtime, queryFn: () => window.docmind.settings.get(), enabled, select: (settings) => settings.runtime });
}

export function useOllamaStatusQuery(enabled = true) {
  return useQuery({
    queryKey: ollamaKeys.status,
    queryFn: () => window.docmind.ollama.status(),
    enabled,
    refetchInterval: (query) => (query.state.data?.available ? 10_000 : false),
    retry: (failureCount, error) => failureCount < 1 && isRetryable(error),
  });
}

export function useOllamaModelsQuery(enabled = true) {
  return useQuery({
    queryKey: ollamaKeys.models,
    queryFn: () => window.docmind.ollama.models(),
    enabled,
    retry: (failureCount, error) => failureCount < 1 && isRetryable(error),
  });
}

const terminalPullStates = new Set(["completed", "failed", "cancelled"]);

export function useOllamaPullQuery(pullId: string | null, enabled = true) {
  const client = useQueryClient();
  const subscribedPullId = useRef<string | null>(null);
  const query = useQuery({
    queryKey: ollamaKeys.pull(pullId ?? ""),
    queryFn: () => window.docmind.ollama.getPull(pullId as string),
    enabled: enabled && Boolean(pullId),
    refetchInterval: (q) => {
      const state = q.state.data?.state;
      return state && !terminalPullStates.has(state) ? 1_000 : false;
    },
    retry: (failureCount, error) => failureCount < 1 && isRetryable(error),
  });
  const pullData = query.data;
  const hasSnapshot = Boolean(pullData);
  const lastSequence = useRef(0);
  useEffect(() => {
    if (!pullId || !enabled || !pullData) return;
    if (subscribedPullId.current === pullId) return;
    subscribedPullId.current = pullId;
    lastSequence.current = pullData.lastEventSequence;
    const sub = window.docmind.ollama.subscribePull(pullId, lastSequence.current, (event) => {
      const payload = event.payload as typeof pullData & { pull?: typeof pullData; sequence?: number };
      const next = payload.pull ?? payload;
      const sequence = payload.sequence ?? event.sequence ?? 0;
      if (!next || sequence <= lastSequence.current) return;
      lastSequence.current = sequence;
      client.setQueryData(ollamaKeys.pull(pullId), next);
      if (terminalPullStates.has(next.state)) void client.invalidateQueries({ queryKey: ollamaKeys.models });
    });
    return () => {
      sub.detach();
      if (subscribedPullId.current === pullId) subscribedPullId.current = null;
    };
  }, [client, enabled, pullId, hasSnapshot]);
  return query;
}

export function useSaveRuntimeMutation() {
  const client = useQueryClient();
  return useMutation({ mutationFn: (input: RuntimeSettingsInput) => window.docmind.settings.saveRuntime(input), onSuccess: (settings) => { client.setQueryData(settingsKeys.root, settings); client.setQueryData(settingsKeys.runtime, settings.runtime); void client.invalidateQueries({ queryKey: ollamaKeys.status }); void client.invalidateQueries({ queryKey: ollamaKeys.models }); } });
}

export function useSaveWebSearchMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: WebSearchSettingsInput) => window.docmind.settings.saveWebSearch(input),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}
