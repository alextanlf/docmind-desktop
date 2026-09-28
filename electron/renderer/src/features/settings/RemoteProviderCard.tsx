import { Globe2 } from "lucide-react";
import type { RemoteProviderSummary } from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import { RemoteSecretChannel } from "./RemoteSecretChannel";
import { RemoteStatus } from "./RemoteStatus";
import { useRemoteCredentialsQuery } from "./settings.queries";

/**
 * One card per registered remote provider, rendered from the registry
 * summary. Channels declared by the provider's credential spec decide the
 * body: browser-login channels reuse `RemoteStatus`, secret channels get
 * the generic credential form.
 */
export function RemoteProviderCard({ provider }: { provider: RemoteProviderSummary }) {
  const credentials = useRemoteCredentialsQuery(provider.name);

  return (
    <article className="connection-card" aria-labelledby={`remote-${provider.name}-title`}>
      <header>
        <Globe2 aria-hidden="true" size={17} />
        <div>
          <h3 id={`remote-${provider.name}-title`}>{provider.label}</h3>
          <p>远程知识库来源</p>
        </div>
        <StatusBadge
          label={provider.configured ? "已连接" : "未连接"}
          tone={provider.configured ? "success" : "neutral"}
        />
      </header>
      {credentials.isPending ? <p className="muted-row">正在检查凭据…</p> : null}
      {credentials.data?.map((channel) =>
        channel.hasSecret ? (
          <RemoteSecretChannel key={channel.channel} channel={channel} />
        ) : (
          <RemoteStatus
            key={channel.channel}
            capabilities={provider.capabilities}
            displayName={provider.label}
            provider={provider.name}
          />
        ),
      )}
    </article>
  );
}
