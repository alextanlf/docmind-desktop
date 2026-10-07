import { Bell, Library } from "lucide-react";
import type {
  RemoteCredentialChannel,
  RemoteProviderSummary,
} from "../../../../shared/contracts";
import { StatusBadge } from "../../components/StatusBadge";
import { RemoteSecretChannel } from "./RemoteSecretChannel";
import { RemoteStatus } from "./RemoteStatus";
import { useRemoteCredentialsQuery } from "./settings.queries";

/**
 * One card per registered remote provider, rendered from the registry summary.
 *
 * The vendor supplies the card title; the channels are grouped by what they
 * *do*, which is the one distinction a user has to make a choice about. A bot
 * webhook sitting next to two document sources used to read as a third way to
 * import documents, and it is not one — it only delivers notifications.
 */
export function RemoteProviderCard({
  provider,
  purpose,
}: {
  provider: RemoteProviderSummary;
  purpose: "source" | "notify";
}) {
  const credentials = useRemoteCredentialsQuery(provider.name);
  const channels = (credentials.data ?? []).filter(
    (channel) =>
      // The response belongs to this provider, but a client that answered with
      // another provider's channels would otherwise render them twice — once
      // per card — so the owner is checked too.
      channel.provider === provider.name &&
      // Default to "source" rather than dropping the channel: a provider that
      // predates the field, or a response that skipped it, must still show up
      // somewhere. Hiding it would look like the provider was removed.
      (channel.purpose ?? "source") === purpose,
  );

  if (credentials.isPending) {
    return (
      <article className="connection-card" aria-labelledby={`remote-${provider.name}-${purpose}`}>
        <header>
          <Library aria-hidden="true" size={17} />
          <div>
            <h3 id={`remote-${provider.name}-${purpose}`}>{provider.label}</h3>
            <p>正在检查连接状态…</p>
          </div>
        </header>
      </article>
    );
  }

  // A provider with nothing in this group is not shown at all: a Feishu bot is
  // not a Yuque capability, and an empty card would only add noise.
  if (channels.length === 0) return null;

  const isNotify = purpose === "notify";
  const Icon = isNotify ? Bell : Library;
  const connected = channels.some((channel) => channel.state === "verified");

  return (
    <article
      className="connection-card"
      aria-labelledby={`remote-${provider.name}-${purpose}-title`}
    >
      <header>
        <Icon aria-hidden="true" size={17} />
        <div>
          <h3 id={`remote-${provider.name}-${purpose}-title`}>{provider.label}</h3>
          <p>{isNotify ? "导入完成时发送通知" : "可导入文档的知识库来源"}</p>
        </div>
        <StatusBadge
          label={connected ? "已连接" : "未连接"}
          tone={connected ? "success" : "neutral"}
        />
      </header>
      {channels.map((channel) => (
        <section className="connection-channel" key={channel.channel}>
          <RemoteChannelBody channel={channel} provider={provider} />
        </section>
      ))}
    </article>
  );
}

function RemoteChannelBody({
  channel,
  provider,
}: {
  channel: RemoteCredentialChannel;
  provider: RemoteProviderSummary;
}) {
  return (
    <>
      <div className="connection-channel-heading">
        <strong>{channel.label}</strong>
        {channel.hint ? <p>{channel.hint}</p> : null}
      </div>
      {channel.hasSecret ? (
        <RemoteSecretChannel channel={channel} />
      ) : (
        <RemoteStatus
          capabilities={provider.capabilities}
          displayName={provider.label}
          provider={provider.name}
        />
      )}
    </>
  );
}
