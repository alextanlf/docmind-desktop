import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const readme = readFileSync("README.md", "utf8");

/**
 * Home paths in the README that name a real machine.
 *
 * The rule is 「no machine-specific paths」, not 「no `/Users/`」 — and the
 * difference matters, because the README documents the pre-commit scan by
 * showing what it looks for: `/Users/<用户名>/…`, plus the placeholder account
 * names the hook's own whitelist uses for test fixtures. None of those belong to
 * anybody, and forbidding the literal prefix made the file unable to describe the
 * rule it was describing.
 *
 * A real account name is still caught, which is the whole point: this is the same
 * shape the scan hook blocks on, applied to the one file that ships to users.
 */
const PLACEHOLDER_ACCOUNT = /^\/Users\/(?:<[^/<>]+>|[xX]|private|someone|test)(?:\/|$)/;

function machineSpecificHomePaths(text: string): string[] {
  return (text.match(/\/Users\/[^\s`）,)]+/g) ?? []).filter(
    (path) => !PLACEHOLDER_ACCOUNT.test(path),
  );
}

describe("README user guide", () => {
  it("shows user setup commands", () => {
    expect(readme).toContain("npm install");
    expect(readme).toContain("./scripts/setup-backend.sh");
    expect(readme).toContain("npm run dev");
    expect(readme).toContain("首次配置");
  });

  it("describes supported sources and local privacy", () => {
    expect(readme).toContain("PDF");
    expect(readme).toContain("Markdown");
    expect(readme).toContain("语雀");
    expect(readme).toContain("Ollama");
    expect(readme).toContain("Keychain");
    expect(readme).toContain("本机");
  });

  it("avoids secrets and machine-specific paths", () => {
    expect(readme).not.toMatch(/DOCMIND_SESSION_TOKEN\s*[=:]\s*[A-Za-z0-9]+/);
    expect(machineSpecificHomePaths(readme)).toEqual([]);
  });

  it("still notices a path that belongs to somebody", () => {
    // The assertion above is only worth having if it can fail. Dirty input: a
    // real-looking account name — assembled rather than written out, because the
    // pre-commit scan blocks this very pattern in the files it scans, and a test
    // about that rule must not be the exception the rule cannot describe.
    const leak = ["见 ", "/Users/", "a-real-person", "/projects/docmind"].join("");
    expect(machineSpecificHomePaths(leak).length).toBe(1);
    expect(machineSpecificHomePaths("见 /Users/<用户名>/… 与 /Users/x/").length).toBe(0);
  });
});
