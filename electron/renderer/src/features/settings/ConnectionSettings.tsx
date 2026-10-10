import { Globe2, KeyRound, MessagesSquare } from "lucide-react";
import type { SettingsView } from "../../../../shared/contracts";
import { FeishuBinding } from "./FeishuBinding";
import { YuqueApiBinding } from "./YuqueApiBinding";
import { YuqueStatus } from "./YuqueStatus";

export function ConnectionSettings({ settings }: { settings: SettingsView }) {
  return (
    <div className="connection-grid">
      <article className="connection-card" aria-labelledby="yuque-web-title">
        <header>
          <Globe2 aria-hidden="true" size={17} />
          <div>
            <h3 id="yuque-web-title">语雀网页</h3>
            <p>通过浏览器登录，作为 API 未启用时的回退通道</p>
          </div>
        </header>
        <YuqueStatus />
      </article>
      <article className="connection-card" aria-labelledby="yuque-api-title">
        <header>
          <KeyRound aria-hidden="true" size={17} />
          <div>
            <h3 id="yuque-api-title">语雀 API</h3>
            <p>使用个人访问令牌连接语雀开放 API</p>
          </div>
        </header>
        <YuqueApiBinding settings={settings} />
      </article>
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
