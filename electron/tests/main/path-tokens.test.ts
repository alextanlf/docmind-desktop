import { describe, expect, it } from "vitest";
import { DIRECTORY_DOMAIN, FILE_DOMAIN, pathToken } from "../../main/path-tokens";

/**
 * 与后端 `backend/tests/document/test_path_tokens.py` **逐字相同的向量**。
 *
 * 两端各自实现 HMAC（这里是 node:crypto，那边是 Python hmac），算法、消息构造或编码
 * 任何一处不一致，都会有一边的断言变红。前后端的路径签名必须逐字节一致 ——
 * 不一致的后果不是"报错"而是"用户选的文件读不了"，所以它值得一条专门的用例钉住。
 *
 * 路径里带中文是故意的：编码一旦按错（比如漏了 utf-8），ASCII 路径照样能通过。
 */
const SHARED_SECRET = "test-session-token";
const SHARED_PATH = "/Users/someone/论文/attention.pdf";
const SHARED_TOKENS: Record<string, string> = {
  [FILE_DOMAIN]: "Ls7zxuVJfxiiBtBRugUXNpTRO7c7x3K6zc35foYy-B4",
  [DIRECTORY_DOMAIN]: "xtNXjE18Bv40nRGW7ll7twLbjiX7dG-ohp38f0TSrfg",
};

describe("pathToken", () => {
  it("produces the exact token the backend expects", () => {
    for (const [domain, expected] of Object.entries(SHARED_TOKENS)) {
      expect(pathToken(SHARED_SECRET, domain, SHARED_PATH)).toBe(expected);
    }
  });

  it("is url-safe and unpadded, so it can travel in the wire as-is", () => {
    const token = pathToken("s", FILE_DOMAIN, "/tmp/a b+c/d");

    expect(token).not.toContain("=");
    expect(token).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("separates the two grants", () => {
    const path = "/Users/someone/资料";

    expect(pathToken("s", DIRECTORY_DOMAIN, path)).not.toBe(pathToken("s", FILE_DOMAIN, path));
  });

  it("binds the token to the path, not to the caller", () => {
    const signed = pathToken(SHARED_SECRET, FILE_DOMAIN, "/Users/someone/公开.pdf");

    expect(pathToken(SHARED_SECRET, FILE_DOMAIN, "/Users/someone/.ssh/id_rsa")).not.toBe(signed);
  });
});
