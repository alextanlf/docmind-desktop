/**
 * What the plugin page derives from a row: its status chip, where it came from,
 * and which actions belong beside it.
 *
 * Kept out of the component because it is exactly the part worth testing on its
 * own, and because it is where any per-source knowledge would accumulate — the
 * component should hold none, the same way it holds no vendor names.
 */
import type { PluginManifest, PluginOrigin } from "../../../../shared/contracts";

/** The badge's tones, as `StatusBadge` declares them. */
export type BadgeTone = "success" | "pending" | "error" | "neutral";

/**
 * Kinds whose row reports a live connection.
 *
 * A format stores no credential, so it has no connection to report and a badge
 * saying 「未连接」 on it would be a claim about something that does not exist.
 */
const CREDENTIAL_KINDS = new Set(["remote_source"]);

export function tracksCredential(kind: string): boolean {
  return CREDENTIAL_KINDS.has(kind);
}

/**
 * The one status a row shows, or `null` for a row with nothing to report.
 *
 * A plugin's own state comes first: it is the fact that explains every other
 * one — a card from a plugin that failed to load is not "未连接", it is a
 * symptom, and saying so is the difference between a user who can fix it and
 * one who re-enters a token that was never the problem.
 */
export function pluginStatus(plugin: PluginManifest): { label: string; tone: BadgeTone } | null {
  const origin = plugin.origin;
  if (origin && !origin.active) {
    return origin.error
      ? { label: "加载失败", tone: "error" }
      : { label: "已停用", tone: "neutral" };
  }
  if (!tracksCredential(plugin.kind)) {
    return { label: "已启用", tone: "success" };
  }
  if (plugin.state === "verified") {
    return { label: "已连接", tone: "success" };
  }
  return plugin.configured
    ? { label: "待验证", tone: "pending" }
    : { label: "未连接", tone: "neutral" };
}

/**
 * Where a row came from, in the words a user would use.
 *
 * An unfamiliar source renders as itself rather than falling back to something
 * reassuring: the same reason an unknown card kind still draws a card.
 */
export function provenanceLabel(origin: PluginOrigin): string {
  switch (origin.source) {
    case "builtin":
      return "随 DocMind 提供";
    case "directory":
      return "插件目录";
    case "distribution":
      return "已安装的插件包";
    default:
      return origin.source;
  }
}

/**
 * Why a plugin offers no remove button, or `null` when it does.
 *
 * Stated rather than silently omitted: a missing control with no explanation
 * reads as a bug, and both reasons here are real constraints rather than
 * omissions — the application installed one, and the packaged runtime ships no
 * installer for the other.
 */
export function removalHint(origin: PluginOrigin | null): string | null {
  if (!origin || origin.removable) return null;
  return origin.toggleable
    ? "这个插件以插件包的形式安装，DocMind 不能替你移除它。"
    : "随 DocMind 提供，不能单独停用或移除。";
}
