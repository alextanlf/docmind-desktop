import { app } from "electron";
import { BackendManager, BackendStartError } from "./backend-manager";
import { createWindow, showAfterDidFinishLoad } from "./window-manager";
import { logger } from "./logger";
let backend: BackendManager;
function parsePackagedArgs(value: string | undefined) {
  if (value === undefined) return { valid: true, args: undefined };
  try {
    const parsed: unknown = JSON.parse(value);
    if (
      !Array.isArray(parsed) ||
      parsed.some((argument) => typeof argument !== "string")
    )
      return { valid: false, args: undefined };
    return { valid: true, args: parsed as string[] };
  } catch {
    return { valid: false, args: undefined };
  }
}
const packagedArgs = parsePackagedArgs(process.env.DOCMIND_BACKEND_ARGS);
app.whenReady().then(async () => {
  try {
    if (app.isPackaged && !packagedArgs.valid) throw new BackendStartError();
    backend = new BackendManager({
      dataDir: app.getPath("userData"),
      repoDir: process.cwd(),
      packaged: app.isPackaged,
      backendCommand: process.env.DOCMIND_BACKEND_COMMAND,
      backendArgs: packagedArgs.args,
      backendCwd: process.env.DOCMIND_BACKEND_CWD,
    });
    await backend.start();
    const origin =
      process.env.ELECTRON_RENDERER_URL ??
      `file://${__dirname}/../renderer/index.html`;
    const win = createWindow(origin);
    showAfterDidFinishLoad(win);
    await win.loadURL(origin);
  } catch (error) {
    logger.error(error);
    app.quit();
  }
});
app.on("before-quit", () => {
  void backend?.stop();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
