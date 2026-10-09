import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RuntimeSettingsInput, SaveRemoteCredentialInput } from "../../../../shared/contracts";
import { clientErrorMessage, isRetryable } from "../../lib/client-errors";

export { clientErrorMessage, errorAction, isRetryable } from "../../lib/client-errors";

export const settingsKeys = {
  root: ["settings"] as const,
  embedding: ["embedding", "status"] as const,
  remote: (provider: string) => ["remote", "status", provider] as const,
  remoteProviders: ["remote", "providers"] as const,
  remoteCredentials: (provider: string) => ["remote", "credentials", provider] as const,
  // The plugin catalogue is invalidated by any credential write, since that is
  // what changes a card's connection state.
  plugins: ["plugins"] as const,
  pluginDiagnostics: ["plugins", "diagnostics"] as const,
  pluginDirectory: ["plugins", "directory"] as const,
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
 * The plugin catalogue, loaded once and filtered client-side.
 *
 * This replaces the old per-provider fan-out (`useRemoteCredentialsQueries`),
 * which existed only so the page could group channels by purpose before
 * rendering. The page no longer groups, and one manifest already carries
 * everything a card needs — including whether to draw a login button or a
 * secret field — so a second round of per-provider requests would be pure
 * overhead.
 */
export function usePluginsQuery() {
  const query = useQuery({
    queryKey: settingsKeys.plugins,
    queryFn: () => window.docmind.plugins.list(),
  });
  return {
    ...query,
    errorMessage: query.error ? clientErrorMessage(query.error) : null,
  };
}

/**
 * Third-party plugins that failed to load. Surfaced in the UI because a plugin
 * that silently failed is indistinguishable from one that was never installed.
 */
export function usePluginDiagnosticsQuery() {
  return useQuery({
    queryKey: settingsKeys.pluginDiagnostics,
    queryFn: () => window.docmind.plugins.diagnostics(),
  });
}

/**
 * The directory a plugin is installed by putting it in.
 *
 * Asked for rather than composed in the renderer: the path follows the
 * application's data directory, which the renderer has no way to know and which
 * is not the same on every platform.
 */
export function usePluginDirectoryQuery() {
  return useQuery({
    queryKey: settingsKeys.pluginDirectory,
    queryFn: () => window.docmind.plugins.directory(),
  });
}

/**
 * Switch a plugin off, or back on.
 *
 * The rows are derived state — a switched-off plugin loses its cards and gains
 * a row of its own — so the catalogue has to be refetched, or the page would go
 * on showing the plugin as though nothing had happened.
 */
export function useSetPluginEnabledMutation(plugin: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => window.docmind.plugins.setEnabled(plugin, enabled),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: settingsKeys.plugins });
      void client.invalidateQueries({ queryKey: settingsKeys.pluginDiagnostics });
    },
  });
}

/**
 * Take a directory plugin out of the plugin directory.
 *
 * Same refetch as the switch, for the same reason: the plugin is no longer
 * installed, so every row that came from it is now wrong.
 */
export function useUninstallPluginMutation(plugin: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.plugins.uninstall(plugin),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: settingsKeys.plugins });
      void client.invalidateQueries({ queryKey: settingsKeys.pluginDiagnostics });
    },
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
      // The card's connection badge is derived state, so it has to be refetched
      // or a successful save leaves the card reading "未连接".
      void client.invalidateQueries({ queryKey: settingsKeys.plugins });
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
      void client.invalidateQueries({ queryKey: settingsKeys.plugins });
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
      void client.invalidateQueries({ queryKey: settingsKeys.plugins });
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

export function useSkipModelSetupMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => window.docmind.settings.skipModelSetup(),
    onSuccess: (settings) => client.setQueryData(settingsKeys.root, settings),
  });
}
