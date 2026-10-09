/**
 * What the plugin page derives from a row.
 *
 * These are the page's rules rather than its markup, so they are asserted
 * directly — and, more usefully, so is their *order*: a plugin's own state has
 * to win over the credential state of one of its cards, or a broken plugin
 * reads as a plugin whose token expired and the user goes looking in the wrong
 * place.
 */
import { describe, expect, it } from "vitest";
import type { PluginManifest, PluginOrigin } from "../../shared/contracts";
import {
  pluginStatus,
  provenanceLabel,
  removalHint,
  tracksCredential,
} from "../../renderer/src/features/settings/plugin-presentation";
import { brokenPlugin, pluginManifests, switchedOffPlugin } from "./test-docmind-api";

const yuque = pluginManifests.find((plugin) => plugin.id === "yuque:web")!;
const tex = pluginManifests.find((plugin) => plugin.id === "tex:core")!;

function withOrigin(origin: Partial<PluginOrigin>): PluginManifest {
  return { ...yuque, origin: { ...yuque.origin!, ...origin } };
}

describe("pluginStatus", () => {
  it("reports a connection only for a kind that has one", () => {
    expect(pluginStatus({ ...yuque, state: "verified" })).toEqual({
      label: "已连接",
      tone: "success",
    });
    expect(pluginStatus({ ...yuque, configured: true, state: "unverified" })).toEqual({
      label: "待验证",
      tone: "pending",
    });
    expect(pluginStatus(yuque)).toEqual({ label: "未连接", tone: "neutral" });
    // A format stores no credential, so a connection badge would be a claim
    // about something that does not exist.
    expect(pluginStatus(tex)).toEqual({ label: "已启用", tone: "success" });
    expect(tracksCredential(tex.kind)).toBe(false);
  });

  it("puts a plugin's own state ahead of its cards'", () => {
    // Dirty input: a card that is also disconnected. The failure is the fact
    // that explains the other one, so it is the one shown.
    expect(pluginStatus(withOrigin({ active: false, error: "boom" }))).toEqual({
      label: "加载失败",
      tone: "error",
    });
    // A switched-off plugin that never loaded. The combination this cannot
    // produce — off *and* failed — is unreachable by construction: a plugin that
    // is off is never imported, so nothing about it can fail.
    expect(pluginStatus(withOrigin({ enabled: false, active: false }))).toEqual({
      label: "已停用",
      tone: "neutral",
    });
  });

  it("reports a switched-off plugin that has no cards of its own", () => {
    expect(pluginStatus(switchedOffPlugin)).toEqual({ label: "已停用", tone: "neutral" });
    expect(pluginStatus(brokenPlugin)).toEqual({ label: "加载失败", tone: "error" });
  });
});

describe("provenanceLabel", () => {
  it("names the three sources the application produces", () => {
    expect(provenanceLabel({ ...yuque.origin!, source: "builtin" })).toBe("随 DocMind 提供");
    expect(provenanceLabel(tex.origin!)).toBe("插件目录");
    expect(provenanceLabel({ ...tex.origin!, source: "distribution" })).toBe("已安装的插件包");
  });

  it("renders an unfamiliar source as itself", () => {
    // Dirty input: a source this build has never heard of. Falling back to
    // something reassuring would be a lie about a third-party plugin's origin.
    expect(provenanceLabel({ ...tex.origin!, source: "side-loaded" })).toBe("side-loaded");
  });
});

describe("removalHint", () => {
  it("says nothing when the plugin can be removed", () => {
    expect(removalHint(tex.origin!)).toBeNull();
  });

  it("explains both reasons a remove button is absent", () => {
    // A missing control with no explanation reads as a bug, and both of these
    // are real constraints rather than omissions.
    expect(removalHint({ ...tex.origin!, source: "distribution", removable: false })).toContain(
      "插件包",
    );
    expect(
      removalHint({ ...tex.origin!, source: "builtin", removable: false, toggleable: false }),
    ).toContain("随 DocMind 提供");
    // Dirty input: a contribution installed with nothing to attribute it to.
    expect(removalHint(null)).toBeNull();
  });
});
