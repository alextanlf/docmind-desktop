import { describe, expect, it } from "vitest";
import { filterPlugins } from "../../renderer/src/features/settings/plugin-filter";
import { pluginManifests } from "./test-docmind-api";

/**
 * The filter is unit-tested on its own because its contract has to match the
 * backend's `GET /api/plugins?q=` exactly. If the two ever diverge, a card the
 * API returns gets hidden by the search box — a bug no integration test would
 * catch, because each side passes in isolation.
 */
describe("插件搜索过滤", () => {
  const ids = (query: string) => filterPlugins(pluginManifests, query).map((p) => p.id);

  it("returns everything for an empty or whitespace query", () => {
    expect(ids("")).toHaveLength(pluginManifests.length);
    expect(ids("   ")).toHaveLength(pluginManifests.length);
  });

  it("matches on the owner across every one of that integration's plugins", () => {
    // The regression this guards: searching a vendor must not hide its other
    // channels. Under the old per-provider cards there was one hit at most.
    expect(ids("飞书")).toEqual(["feishu:app", "feishu:user", "feishu:webhook"]);
    expect(ids("语雀")).toEqual(["yuque:web", "yuque:api"]);
  });

  it("matches aliases that appear in no label", () => {
    // "lark" is only in a declared keyword. Without keywords being part of the
    // haystack this returns nothing at all.
    expect(ids("lark")).toEqual(["feishu:app", "feishu:user", "feishu:webhook"]);
    expect(ids("wiki")).toContain("yuque:api");
    expect(ids("wiki")).toContain("acme:token");
  });

  it("is case insensitive", () => {
    expect(ids("ACME")).toEqual(["acme:token"]);
  });

  it("ands multiple terms so extra words narrow the result", () => {
    expect(ids("语雀 令牌")).toEqual(["yuque:api"]);
    expect(ids("acme nonexistent")).toEqual([]);
  });

  it("searches the summary, not just the title", () => {
    expect(ids("浏览器里登录")).toEqual(["yuque:web"]);
  });
});