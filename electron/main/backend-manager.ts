import { randomBytes } from "node:crypto";
import { spawn as nodeSpawn, ChildProcess } from "node:child_process";
import { appendFile } from "node:fs/promises";
import { join } from "node:path";
import { resolveBackendRuntime, type BackendRuntimeContract } from "./backend-runtime-contract";
import { isE2ERuntime } from "./e2e-runtime";
import { redactSecrets } from "./redaction";

export interface BackendConnection {
  baseUrl: string;
  token: string;
}
type SpawnFn = (
  command: string,
  args: string[],
  options: { cwd?: string; env?: NodeJS.ProcessEnv; stdio?: unknown },
) => ChildProcess;
export class BackendStartError extends Error {
  code = "BACKEND_START_FAILED";
  constructor(message = "Backend failed to start") {
    super(message);
    this.name = "BackendStartError";
  }
}

export class BackendManager {
  private child?: ChildProcess;
  private connection?: BackendConnection;
  private childExited = false;
  private stderr = "";
  private token = "";
  private readonly spawn: SpawnFn;
  private readonly fetchFn: typeof fetch;
  private readonly dataDir: string;
  private readonly interval: number;
  private readonly timeout: number;
  private readonly shutdownTimeout: number;
  private readonly runtime: BackendRuntimeContract;
  private readonly configurationError?: BackendStartError;
  private readonly bundledModelsDir?: string;
  /** Resolved once so every artifact write obeys the same E2E gate. */
  private readonly e2eRuntime: boolean;
  private lifecycleTail?: Promise<void>;
  constructor(opts: {
    spawn?: SpawnFn;
    fetch?: typeof fetch;
    dataDir?: string;
    healthIntervalMs?: number;
    startupTimeoutMs?: number;
    shutdownTimeoutMs?: number;
    repoDir?: string;
    packaged?: boolean;
    platform?: string;
    backendCommand?: string;
    backendArgs?: string[];
    backendCwd?: string;
    bundledModelsDir?: string;
  }) {
    this.spawn = opts.spawn ?? (nodeSpawn as SpawnFn);
    this.fetchFn = opts.fetch ?? fetch;
    this.interval = opts.healthIntervalMs ?? 100;
    this.timeout = opts.startupTimeoutMs ?? 15000;
    this.shutdownTimeout = opts.shutdownTimeoutMs ?? 3000;
    this.bundledModelsDir = opts.bundledModelsDir;
    const packaged = opts.packaged ?? false;
    this.e2eRuntime = isE2ERuntime(process.env, packaged);
    try {
      this.runtime = resolveBackendRuntime(
        // 只有 macOS 打包态才会从 Info.plist 的 LSEnvironment 拿到这三个值；
        // Windows / Linux 没有对应机制，传 undefined 让契约层走 resourcesPath 自解析。
        packaged
          ? {
              DOCMIND_BACKEND_COMMAND: opts.backendCommand,
              DOCMIND_BACKEND_ARGS:
                opts.backendArgs === undefined ? undefined : JSON.stringify(opts.backendArgs),
              DOCMIND_BACKEND_CWD: opts.backendCwd,
            }
          : {},
        {
          packaged,
          repoDir: opts.repoDir ?? process.cwd(),
          dataDir: opts.dataDir ?? "",
          // packaged 模式下从 resourcesPath 解析后端路径：
          // macOS 是 <app>/Contents/Resources，Windows 是 <安装目录>/resources。
          resourcesPath: packaged ? process.resourcesPath : undefined,
          platform: opts.platform ?? process.platform,
        },
      );
    } catch {
      this.runtime = {
        command: "",
        args: [],
        cwd: "",
        dataDir: opts.dataDir ?? "",
        port: 18900,
      };
      this.configurationError = new BackendStartError();
    }
    this.dataDir = this.runtime.dataDir;
  }
  private runLifecycleOperation<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.lifecycleTail ? this.lifecycleTail.then(operation, operation) : operation();
    const tail = result.then(
      () => undefined,
      () => undefined,
    );
    this.lifecycleTail = tail;
    void tail.then(() => {
      if (this.lifecycleTail === tail) this.lifecycleTail = undefined;
    });
    return result;
  }
  start(): Promise<BackendConnection> {
    return this.runLifecycleOperation(() => this.startOnce());
  }
  private async startOnce(): Promise<BackendConnection> {
    if (this.connection) return this.connection;
    if (this.configurationError) throw this.configurationError;
    this.token = randomBytes(32).toString("hex");
    const env = {
      ...process.env,
      DOCMIND_SESSION_TOKEN: this.token,
      DOCMIND_DATA_DIR: this.dataDir,
      DOCMIND_PORT: String(this.runtime.port),
      ...(this.bundledModelsDir
        ? { DOCMIND_BUNDLED_MODELS_DIR: this.bundledModelsDir }
        : {}),
    };
    let startError: Error | undefined;
    let exited = false;
    this.childExited = false;
    try {
      this.child = this.spawn(this.runtime.command, this.runtime.args, {
        cwd: this.runtime.cwd,
        env,
        stdio: ["ignore", "ignore", "pipe"],
      });
    } catch {
      this.reset();
      throw new BackendStartError();
    }
    const child = this.child;
    child.stderr?.on("data", (data: Buffer | string) => {
      this.stderr = (this.stderr + String(data)).slice(-2000);
      const artifacts = process.env.DOCMIND_E2E_ARTIFACTS_DIR;
      if (this.e2eRuntime && artifacts)
        void appendFile(join(artifacts, "backend.log"), String(data), "utf8").catch(() => {});
    });
    child.once("error", () => {
      // Defer diagnostic construction until the startup loop observes the
      // failure so stderr data delivered after the error event is retained.
      startError = new BackendStartError();
    });
    child.once("exit", () => {
      exited = true;
      this.childExited = true;
      const artifacts = process.env.DOCMIND_E2E_ARTIFACTS_DIR;
      if (this.e2eRuntime && artifacts)
        void appendFile(join(artifacts, "backend.log"), "backend exited\n", "utf8").catch(() => {});
    });
    const deadline = Date.now() + this.timeout;
    while (Date.now() < deadline) {
      if (startError || exited) {
        const diagnostic = this.redactedError();
        await this.stopOnce();
        throw new BackendStartError(diagnostic);
      }
      try {
        const response = await this.healthRequest(
          () => startError ?? (exited ? new BackendStartError(this.redactedError()) : undefined),
          deadline - Date.now(),
        );
        if (response.ok) {
          this.connection = {
            baseUrl: `http://127.0.0.1:${this.runtime.port}`,
            token: this.token,
          };
          return this.connection;
        }
      } catch {
        /* wait for backend */
      }
      const remaining = deadline - Date.now();
      if (remaining <= 0) break;
      await new Promise((resolve) => setTimeout(resolve, Math.min(this.interval, remaining)));
    }
    const diagnostic = this.redactedError();
    await this.stopOnce();
    throw new BackendStartError(diagnostic);
  }
  private async healthRequest(failure: () => Error | undefined, timeoutMs: number) {
    let rejectChild: (error: Error) => void = () => {};
    const childFailure = new Promise<Response>((_, reject) => {
      rejectChild = reject;
    });
    const controller = new AbortController();
    let rejectDeadline: (error: Error) => void = () => {};
    const probeDeadline = new Promise<Response>((_, reject) => {
      rejectDeadline = reject;
    });
    const deadlineTimer = setTimeout(
      () => {
        controller.abort();
        rejectDeadline(new BackendStartError());
      },
      Math.max(0, timeoutMs),
    );
    const check = setInterval(() => {
      const error = failure();
      if (error) rejectChild(error);
    }, 10);
    try {
      return await Promise.race([
        this.fetchFn(`http://127.0.0.1:${this.runtime.port}/health`, {
          headers: { "X-DocMind-Token": this.token },
          signal: controller.signal,
        }),
        childFailure,
        probeDeadline,
      ]);
    } finally {
      clearTimeout(deadlineTimer);
      clearInterval(check);
    }
  }
  private redactedError() {
    const safe = redactSecrets(this.stderr.replaceAll(this.token, "[redacted]"));
    return safe ? `Backend failed to start: ${safe}` : "Backend failed to start";
  }
  private reset() {
    this.child = undefined;
    this.connection = undefined;
    this.stderr = "";
    this.token = "";
  }
  async request(path: string, init: RequestInit = {}) {
    if (!this.connection) throw new Error("BACKEND_NOT_STARTED");
    const headers = new Headers(init.headers);
    headers.set("X-DocMind-Token", this.connection.token);
    return this.fetchFn(`${this.connection.baseUrl}${path}`, {
      ...init,
      headers,
    });
  }
  stop(): Promise<boolean> {
    return this.runLifecycleOperation(() => this.stopOnce());
  }
  private async stopOnce(): Promise<boolean> {
    const child = this.child;
    const childExited = this.childExited;
    this.reset();
    if (!child?.pid || childExited) return true;
    const waitForExit = () =>
      new Promise<boolean>((resolve) => {
        let settled = false;
        const timer = setTimeout(() => finish(false), this.shutdownTimeout);
        const finish = (value: boolean) => {
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve(value);
          }
        };
        child.once("exit", () => finish(true));
      });
    const waitForExitAfterSignal = async (signal: NodeJS.Signals) => {
      const exit = waitForExit();
      try {
        child.kill(signal);
      } catch {
        return true;
      }
      return exit;
    };
    const exited = await waitForExitAfterSignal("SIGTERM");
    if (exited) return true;
    if (!exited) {
      return waitForExitAfterSignal("SIGKILL");
    }
    return true;
  }
}
