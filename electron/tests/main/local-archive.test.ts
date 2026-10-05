import { existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { describe, expect, it } from "vitest";

describe("local archive gate", () => {
  it("reports OPTIONAL / NOT BUILT and creates no artifacts when disabled", () => {
    const output = mkdtempSync(join(tmpdir(), "docmind-local-archive-"));
    try {
      const result = spawnSync("bash", ["scripts/archive-local.sh"], {
        cwd: process.cwd(),
        env: {
          ...process.env,
          DOCMIND_ENABLE_LOCAL_ARCHIVE: "0",
          DOCMIND_BUILD_LOCAL_ARCHIVE: "1",
          DOCMIND_ARCHIVE_DIR: output,
        },
        encoding: "utf8",
      });

      expect(result.status).toBe(0);
      expect(result.stdout).toContain("OPTIONAL / NOT BUILT");
      expect(readdirSync(output)).toEqual([]);
    } finally {
      rmSync(output, { recursive: true, force: true });
    }
  });

  // DOCMIND_BUILD_LOCAL_ARCHIVE used to be read by nobody, so the case above
  // passed for the wrong reason: it could not tell "gate honoured" apart from
  // "variable ignored". These two cases pin the actual semantics.
  it("skips the build when enabled but explicitly not asked to build", () => {
    const output = mkdtempSync(join(tmpdir(), "docmind-local-archive-"));
    try {
      const result = spawnSync("bash", ["scripts/archive-local.sh"], {
        cwd: process.cwd(),
        env: {
          ...process.env,
          DOCMIND_ENABLE_LOCAL_ARCHIVE: "1",
          DOCMIND_BUILD_LOCAL_ARCHIVE: "0",
          DOCMIND_ARCHIVE_DIR: output,
        },
        encoding: "utf8",
      });

      // A `npm run desktop:build` here would have produced console noise and
      // taken minutes; the script must honour the flag instead.
      expect(result.stdout).not.toContain("vite");
      expect(result.stdout).toContain("OPTIONAL / NOT SIGNED");
    } finally {
      rmSync(output, { recursive: true, force: true });
    }
  });

  it("fails when enabled, not building, and no compiled output exists", () => {
    const output = mkdtempSync(join(tmpdir(), "docmind-local-archive-"));
    const root = process.cwd();
    // The script resolves its own root, so `out/` cannot be relocated. Skip
    // rather than assert a branch that depends on whether the repo happens to
    // be built — a test whose result flips with local state is not a test.
    if (existsSync(join(root, "out"))) return;
    try {
      const result = spawnSync("bash", ["scripts/archive-local.sh"], {
        cwd: root,
        env: {
          ...process.env,
          DOCMIND_ENABLE_LOCAL_ARCHIVE: "1",
          DOCMIND_BUILD_LOCAL_ARCHIVE: "0",
          DOCMIND_ARCHIVE_DIR: output,
        },
        encoding: "utf8",
      });

      expect(result.status).toBe(1);
      expect(result.stderr).toContain("缺少编译产物");
    } finally {
      rmSync(output, { recursive: true, force: true });
    }
  });
});
