import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  RemoteCredentialChannel,
  RuntimeSettingsInput,
  SaveRemoteCredentialInput,
  WebSearchSettingsInput,
} from "../../../../shared/contracts";
import { isRetryable } from "../../lib/client-errors";

export { clientErrorMessage, errorAction, isRetryable } from "../../lib/client-errors";

export const settingsKeys = {
  root: ["settings"] as const,
  embedding: ["embedding", "status"] as const,
  remote: (provider: string) => ["remote", "status", provider] as const,
  remoteProviders: ["remote", "providers"] as const,
  remoteCredentials: (provider: string) => ["remote", "credentials", provider] as const,
};
export const localModelKeys = {
  status: ["localModel", "status"] as const,
  models: ["localModel", "models"] as const,
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

/**
 * Every provider's channels in one shot, keyed by provider name.
 *
 * The settings page has to know all channels before it can group them, and it
 * needs the answer to decide which cards to render at all — so issuing the
 * requests from here keeps that decision in the query layer instead of making
 * the component fan out. The per-provider hook stays for the single-provider
 * mutations that invalidate these keys.
 */
export function useRemoteCredentialsQueries(providers: string[]) {
  const results = useQueries({
    queries: providers.map((provider) => ({
      queryKey: settingsKeys.remoteCredentials(provider),
      queryFn: () => window.docmind.remote.listCredentials(provider),
    })),
  });
  const data: Record<string, RemoteCredentialChannel[]> = {};
  providers.forEach((provider, index) => {
    const channels = results[index]?.data;
    if (channels) data[provider] = channels;
  });
  return {
    data: Object.keys(data).length > 0 ? data : undefined,
    isPending: results.some((result) => result.isPending),
  };
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

export function useLocalModelStatusQuery(enabled = true) {
  return useQuery({
    queryKey: localModelKeys.status,
    queryFn: () => window.docmind.localModel.status(),
    enabled,
    refetchInterval: (query) => (query.state.data?.available ? 10_000 : false),
    retry: (failureCount, error) => failureCount < 1 && isRetryable(error),
  });
}

export function useLocalModelsQuery(enabled = true) {
  return useQuery({
    queryKey: localModelKeys.models,
    queryFn: () => window.docmind.localModel.models(),
    enabled,
    retry: (failureCount, error) => failureCount < 1 && isRetryable(error),
  });
}

export function useSaveRuntimeMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: RuntimeSettingsInput) => window.docmind.settings.saveRuntime(input),
    onSuccess: (settings) => {
      client.setQueryData(settingsKeys.root, settings);
      void client.invalidateQueries({ queryKey: localModelKeys.status });
      void client.invalidateQueries({ queryKey: localModelKeys.models });
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

export function useSkipModelSetupMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.settings.skipModelSetup(),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}
