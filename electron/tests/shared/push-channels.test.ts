import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { IPC_CHANNELS, PUSH_CHANNELS } from "../../shared/channels";

const here = dirname(fileURLToPath(import.meta.url));
const preloadSource = readFileSync(resolve(here, "../../preload/index.ts"), "utf8");

/**
 * send/handle 注册错配是静默失效：`ipcMain.handle` 注册的处理器**永远不会被**
 * `ipcRenderer.send` 触发，表现为「进度条永远不动」而非任何报错。
 * 这里从 preload 源码里反解出所有 send 出去的通道，与 PUSH_CHANNELS 对账，
 * 使「新增推送通道忘了登记」变成一条会红的测试。
 */
function channelsSentViaRenderer(): string[] {
  // subscription() 内部统一 `ipcRenderer.send(channel, ...)`；
  // streamCancel 也直接 send。
  const calls = [
    ...preloadSource.matchAll(/subscription\(\s*IPC_CHANNELS\.(\w+)/g),
    ...preloadSource.matchAll(/ipcRenderer\.send\(\s*IPC_CHANNELS\.(\w+)/g),
  ].map((m) => m[1]);
  return [...new Set(calls)];
}

describe("推送通道注册契约", () => {
  it("preload 用 send 派发的通道全部登记为推送通道", () => {
    const sent = channelsSentViaRenderer();

    expect(sent.length).toBeGreaterThan(0);
    for (const key of sent) {
      const channel = IPC_CHANNELS[key as keyof typeof IPC_CHANNELS];
      expect(PUSH_CHANNELS.has(channel)).toBe(true);
    }
  });

  it("推送通道集合不含普通请求通道", () => {
    for (const channel of [
      IPC_CHANNELS.settingsGet,
      IPC_CHANNELS.repositoriesList,
      IPC_CHANNELS.batchesCreate,
      IPC_CHANNELS.documentsRead,
    ]) {
      expect(PUSH_CHANNELS.has(channel)).toBe(false);
    }
  });

  it("集合成员不重复", () => {
    const values = [...PUSH_CHANNELS];
    expect(new Set(values).size).toBe(values.length);
  });
});