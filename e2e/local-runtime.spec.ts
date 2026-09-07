import { expect, test } from "./fixtures/backend-fixture";

test("runs an isolated local runtime lifecycle", async ({ electronApp }) => {
  const record = await electronApp.runLocalRuntimeSmoke();
  expect(record).toEqual({
    mode: "dev",
    dataDirKind: "temporary",
    health: "PASS",
    rendererLoaded: "PASS",
    backendExited: "PASS",
    portReleased: "PASS",
  });
});
