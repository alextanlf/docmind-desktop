import { describe, expect, it } from "vitest";
import { BatchProgressPayloadSchema } from "../../shared/contracts";

describe("BatchProgressPayloadSchema", () => {
  it("accepts the complete nested batch progress envelope", () => {
    expect(
      BatchProgressPayloadSchema.safeParse({
        progress: 50,
        state: "running",
        message: "处理中",
        stage: "running",
        counts: { total: 2, selected: 2, completed: 1, failed: 0, skipped: 0 },
        itemId: null,
        itemState: null,
      }).success,
    ).toBe(true);
  });

  it("rejects out-of-contract stages", () => {
    expect(
      BatchProgressPayloadSchema.safeParse({
        progress: 0,
        state: "running",
        message: "处理中",
        stage: "import",
        counts: { total: 0, selected: 0, completed: 0, failed: 0, skipped: 0 },
        itemId: null,
        itemState: null,
      }).success,
    ).toBe(false);
  });
});
