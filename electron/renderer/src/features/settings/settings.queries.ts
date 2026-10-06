import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  FeishuBindingInput,
  RuntimeSettingsInput,
  SaveRemoteCredentialInput,
  WebSearchSettingsInput,
} from "../../../../shared/contracts";
import { isRetryable } from "./ollama-errors";

export { clientErrorMessage, errorAction, isRetryable } from "./ollama-errors";

export const settingsKeys = {
  root: ["settings"] as const,
  embedding: ["embedding", "status"] as const,
  remote: (provider: string) => ["remote", "status", provider] as const,
  remoteProviders: ["remote", "providers"] as const,
  remoteCredentials: (provider: string) => ["remote", "credentials", provider] as const,
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

export function useRemoteStatusQuery(provider: string) {
  return useQuery({
    queryKey: settingsKeys.remote(provider),
    queryFn: () => window.docmind.remote.status(provider),
  });
}

export function useRemoteProvidersQuery() {
  return useQuery({
    queryKey: settingsKeys.remoteProviders,
    queryFn: () => window.docmind.remote.listProviders(),
  });
}

export function useRemoteCredentialsQuery(provider: string) {
  return useQuery({
    queryKey: settingsKeys.remoteCredentials(provider),
    queryFn: () => window.docmind.remote.listCredentials(provider),
  });
}

export function useSaveRemoteCredentialMutation(provider: string, channel: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveRemoteCredentialInput) =>
      window.docmind.remote.saveCredential(provider, channel, input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: settingsKeys.remoteCredentials(provider) });
      void client.invalidateQueries({ queryKey: settingsKeys.remoteProviders });
    },
  });
}

export function useTestRemoteCredentialMutation(provider: string, channel: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.remote.testCredential(provider, channel),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: settingsKeys.remoteCredentials(provider) });
      void client.invalidateQueries({ queryKey: settingsKeys.remoteProviders });
      void client.invalidateQueries({ queryKey: settingsKeys.remote(provider) });
    },
  });
}

export function useDeleteRemoteCredentialMutation(provider: string, channel: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.remote.deleteCredential(provider, channel),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: settingsKeys.remoteCredentials(provider) });
      void client.invalidateQueries({ queryKey: settingsKeys.remoteProviders });
      void client.invalidateQueries({ queryKey: settingsKeys.remote(provider) });
    },
  });
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
      const payload = event.payload as typeof pullData & {
        pull?: typeof pullData;
        sequence?: number;
      };
      const next = payload.pull ?? payload;
      const sequence = payload.sequence ?? event.sequence ?? 0;
      if (!next || sequence <= lastSequence.current) return;
      lastSequence.current = sequence;
      client.setQueryData(ollamaKeys.pull(pullId), next);
      if (terminalPullStates.has(next.state))
        void client.invalidateQueries({ queryKey: ollamaKeys.models });
    });
    return () => {
      sub.detach();
      if (subscribedPullId.current === pullId) subscribedPullId.current = null;
    };
    // The subscription is intentionally keyed to snapshot availability, not each snapshot object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, enabled, pullId, hasSnapshot]);
  return query;
}

export function useSaveRuntimeMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: RuntimeSettingsInput) => window.docmind.settings.saveRuntime(input),
    onSuccess: (settings) => {
      client.setQueryData(settingsKeys.root, settings);
      void client.invalidateQueries({ queryKey: ollamaKeys.status });
      void client.invalidateQueries({ queryKey: ollamaKeys.models });
    },
  });
}

export function useSaveWebSearchMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: WebSearchSettingsInput) => window.docmind.settings.saveWebSearch(input),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}

export function useSaveFeishuMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: FeishuBindingInput) => window.docmind.settings.saveFeishu(input),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}

export function useTestFeishuMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.settings.testFeishu(),
    onSuccess: () => client.invalidateQueries({ queryKey: settingsKeys.root }),
  });
}

export function useSkipModelSetupMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.settings.skipModelSetup(),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}
