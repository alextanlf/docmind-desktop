import { MessagesSquare } from "lucide-react";
import type { SettingsView } from "../../../../shared/contracts";
import { FeishuBinding } from "./FeishuBinding";
import { RemoteProviderCard } from "./RemoteProviderCard";
import { useRemoteProvidersQuery } from "./settings.queries";

export function ConnectionSettings({ settings }: { settings: SettingsView }) {
  const providers = useRemoteProvidersQuery();

  return (
    <div className="connection-grid">
      {providers.isPending ? <p className="muted-row">正在加载远程来源…</p> : null}
      {providers.data?.map((provider) => (
        <RemoteProviderCard key={provider.name} provider={provider} />
      ))}
      <article className="connection-card" aria-labelledby="feishu-binding-title">
        <header>
          <MessagesSquare aria-hidden="true" size={17} />
          <div>
            <h3 id="feishu-binding-title">飞书绑定</h3>
            <p>绑定飞书自定义机器人，用于通知与应用集成</p>
          </div>
        </header>
        <FeishuBinding settings={settings} />
      </article>
    </div>
  );
}
