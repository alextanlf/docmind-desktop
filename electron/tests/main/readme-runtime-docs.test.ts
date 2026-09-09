import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const readme = readFileSync("README.md", "utf8");

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
    expect(readme).not.toContain("/Users/");
  });
});
