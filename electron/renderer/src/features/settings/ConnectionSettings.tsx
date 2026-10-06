import { RemoteProviderCard } from "./RemoteProviderCard";
import { useRemoteProvidersQuery } from "./settings.queries";

export function ConnectionSettings() {
  const providers = useRemoteProvidersQuery();

  return (
    <div className="connection-grid">
      {providers.isPending ? <p className="muted-row">正在加载远程来源…</p> : null}
      {providers.data?.map((provider) => (
        <RemoteProviderCard key={provider.name} provider={provider} />
      ))}
    </div>
  );
}