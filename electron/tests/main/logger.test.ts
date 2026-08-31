import { describe, expect, it, vi } from "vitest";
import { logger } from "../../main/logger";

describe("startup logging redaction", () => {
  it("does not emit runtime token or filesystem path", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error(
      "DOCMIND_SESSION_TOKEN=abcdef0123456789",
      "/private/secret/path",
    );
    expect(spy.mock.calls.flat().join(" ")).not.toContain("abcdef0123456789");
    expect(spy.mock.calls.flat().join(" ")).not.toContain(
      "/private/secret/path",
    );
    spy.mockRestore();
  });
  it("redacts Error message and stack", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    logger.error(
      new Error(
        "token abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789 at /private/secret",
      ),
    );
    expect(spy.mock.calls.flat().join(" ")).not.toContain("/private/secret");
    spy.mockRestore();
  });
});
