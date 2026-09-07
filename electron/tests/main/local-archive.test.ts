import { mkdtempSync, readdirSync, rmSync } from "node:fs";
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
});
