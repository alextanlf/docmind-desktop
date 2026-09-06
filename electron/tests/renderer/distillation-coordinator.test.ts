import { expect, it } from "vitest";
import { DistillationCoordinator } from "../../renderer/src/features/memory/distillation-coordinator";

it.each([false, true])(
  "cleans settled tails without deleting pending successors, failure=%s",
  async (fail) => {
    const coordinator = new DistillationCoordinator();
    let finish!: () => void;
    const first = coordinator.enqueue("draft", async () => {
      if (fail) throw new Error("failed");
    });
    const second = coordinator.enqueue(
      "draft",
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    await first.catch(() => undefined);
    expect(coordinator.pendingCount).toBe(1);
    await Promise.resolve();
    finish();
    await second;
    expect(coordinator.pendingCount).toBe(0);
    await coordinator.enqueue("draft", async () => undefined);
    expect(coordinator.pendingCount).toBe(0);
  },
);

it("cleans a rejected final operation and permits reuse", async () => {
  const coordinator = new DistillationCoordinator();
  await expect(
    coordinator.enqueue("draft", async () => {
      throw new Error("failed");
    }),
  ).rejects.toThrow("failed");
  expect(coordinator.pendingCount).toBe(0);
  await expect(coordinator.enqueue("draft", async () => "saved")).resolves.toBe("saved");
  expect(coordinator.pendingCount).toBe(0);
});
