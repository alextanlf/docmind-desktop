import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const readme = readFileSync("README.md", "utf8");

describe("README local runtime workflow", () => {
  it("documents first-run and daily commands separately", () => {
    expect(readme).toContain("npm install");
    expect(readme).toContain("./scripts/setup-backend.sh");
    expect(readme).toContain("npm run dev");
    expect(readme).toMatch(/首次安装[\s\S]*日常启动/);
  });

  it("states local data isolation, external dependencies, and archive boundary", () => {
    expect(readme).toContain("DOCMIND_DATA_DIR");
    expect(readme).toContain("临时");
    expect(readme).toContain("Playwright Chromium");
    expect(readme).toContain("Ollama");
    expect(readme).toContain("OPTIONAL / NOT BUILT");
    expect(readme).toContain("未签名");
    expect(readme).toContain("不能承诺其他用户下载后");
  });

  it("explains desktop build is compile-only and avoids secrets", () => {
    expect(readme).toContain("desktop:build");
    expect(readme).toContain("只编译");
    expect(readme).not.toMatch(/DOCMIND_SESSION_TOKEN\s*[=:]\s*[A-Za-z0-9]+/);
    expect(readme).not.toContain("/Users/");
  });
});
