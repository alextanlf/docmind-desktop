import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { clientErrorMessage } from "../../renderer/src/lib/client-errors";

const here = dirname(fileURLToPath(import.meta.url));
const backendAppDir = resolve(here, "../../../backend/app");

/** Every `DomainError` raise site in the backend, by scanning source. */
function backendErrorCodes(dir: string): Set<string> {
  const codes = new Set<string>();
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      for (const code of backendErrorCodes(full)) codes.add(code);
      continue;
    }
    if (!entry.endsWith(".py")) continue;
    const source = readFileSync(full, "utf8");
    // `DomainError(\n    "CODE",` and `DomainError("CODE",` are both used.
    for (const match of source.matchAll(/DomainError\(\s*\n?\s*"([A-Z_]+)"/g)) {
      codes.add(match[1]);
    }
  }
  return codes;
}

const PLATFORM_PREFIXES = ["FEISHU", "YUQUE", "REMOTE"];

describe("平台错误码必须在前端有对应文案", () => {
  // An unregistered code silently degrades: `clientErrorMessage` falls back to
  // a generic "操作失败，请检查设置后重试" that discards the backend's precise
  // message. That is how a Feishu login failure with an actionable explanation
  // reached users as a bare "操作失败".
  const codes = [...backendErrorCodes(backendAppDir)]
    .filter((code) => PLATFORM_PREFIXES.some((prefix) => code.startsWith(prefix)))
    .sort();

  it("后端确实存在这些平台错误码（守卫扫描本身有效）", () => {
    expect(codes.length).toBeGreaterThan(20);
  });

  it.each(codes)("%s 不再退化为通用文案", (code) => {
    const message = clientErrorMessage({ code, message: "backend detail" });
    expect(message).not.toBe("操作失败，请检查设置后重试");
    expect(message).not.toBe(code);
  });

  it("未登记码的 fallback 行为本身保持稳定", () => {
    // If this ever changes, the assertion above becomes vacuous.
    expect(clientErrorMessage({ code: "TOTALLY_UNKNOWN_CODE" })).toBe(
      "操作失败，请检查设置后重试",
    );
  });
});
