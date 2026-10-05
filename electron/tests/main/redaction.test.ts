import { describe, expect, it } from "vitest";
import { redactSecrets } from "../../main/redaction";

describe("redactSecrets", () => {
  it("masks the session token in every occurrence", () => {
    expect(redactSecrets("DOCMIND_SESSION_TOKEN=abc123")).toBe(
      "DOCMIND_SESSION_TOKEN=[redacted]",
    );
    expect(redactSecrets("first DOCMIND_SESSION_TOKEN=a second DOCMIND_SESSION_TOKEN=b")).toBe(
      "first DOCMIND_SESSION_TOKEN=[redacted] second DOCMIND_SESSION_TOKEN=[redacted]",
    );
  });

  // The security-critical invariant: redaction must hold no matter what else is
  // in the line, including quoted paths and other env assignments.
  it("never lets a token survive alongside other content", () => {
    const masked = redactSecrets(
      "spawn '/Applications/My App/run backend' with DOCMIND_SESSION_TOKEN=xyz789 done",
    );
    expect(masked).not.toContain("xyz789");
    expect(masked).toBe("spawn '[path]' with DOCMIND_SESSION_TOKEN=[redacted] done");
  });

  it("masks a bare POSIX path up to the next separator", () => {
    expect(redactSecrets("failed at /Users/someone/lib/db.sqlite3")).toBe("failed at [path]");
  });

  it("masks Windows backslash paths, which neither historical pattern matched", () => {
    expect(redactSecrets("cannot open C:\\Users\\someone\\AppData\\roaming\\db")).toBe(
      "cannot open [path]",
    );
  });

  it("masks a quoted path whole even when segments contain spaces", () => {
    expect(redactSecrets("spawn '/Applications/My App/run backend' failed")).toBe(
      "spawn '[path]' failed",
    );
    expect(redactSecrets('cwd is "/Users/x/Library/Application Support/y.db" now')).toBe(
      'cwd is "[path]" now',
    );
  });

  it("keeps a URL's scheme and authority so the failing endpoint stays identifiable", () => {
    expect(redactSecrets("GET https://example.com/api/v1/models")).toBe(
      "GET https://example.com[path]",
    );
  });

  // Documented limitation, asserted so a future change to the pattern cannot
  // silently alter it: an *unquoted* path with a space in a segment is masked
  // only up to the first space. Path segments are not secrets, and over-greedy
  // matching would swallow the surrounding sentence.
  it("masks an unquoted spaced path only up to the first space", () => {
    expect(redactSecrets("failed at /Users/x/Library/Application Support/y.db")).toBe(
      "failed at [path] Support[path]",
    );
  });

  it("leaves bare hex alone by default", () => {
    const hex = "a".repeat(64);
    expect(redactSecrets(`digest ${hex}`)).toBe(`digest ${hex}`);
  });

  it("masks bare hex when digests are requested", () => {
    const hex = "f".repeat(64);
    expect(redactSecrets(`digest ${hex}`, { digests: true })).toBe("digest [redacted]");
  });

  it("leaves non-path text untouched", () => {
    expect(redactSecrets("chunk 3 of 12 confidence 0.91")).toBe(
      "chunk 3 of 12 confidence 0.91",
    );
  });
});
