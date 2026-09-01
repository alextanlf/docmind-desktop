import { randomBytes } from "node:crypto";
import { spawn as nodeSpawn, ChildProcess } from "node:child_process";
import { appendFile } from "node:fs/promises";
import { join } from "node:path";

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
  private readonly cwd: string;
  private readonly command: string;
  private readonly args: string[];
  private readonly configuredPackagedCommand: boolean;
  private readonly invalidPackagedArgs: boolean;
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
    backendCommand?: string;
    backendArgs?: string[];
    backendCwd?: string;
  }) {
    this.spawn = opts.spawn ?? (nodeSpawn as SpawnFn);
    this.fetchFn = opts.fetch ?? fetch;
    this.dataDir = opts.dataDir ?? "";
    this.interval = opts.healthIntervalMs ?? 100;
    this.timeout = opts.startupTimeoutMs ?? 15000;
    this.shutdownTimeout = opts.shutdownTimeoutMs ?? 3000;
    const packaged = opts.packaged ?? false;
    const rawBackendArgs = opts.backendArgs as unknown;
    const validBackendArgs =
      rawBackendArgs === undefined ||
      (Array.isArray(rawBackendArgs) &&
        Array.from(rawBackendArgs).every((arg) => typeof arg === "string"));
    this.invalidPackagedArgs = packaged && !validBackendArgs;
    this.cwd = packaged ? (opts.backendCwd ?? "") : join(opts.repoDir ?? process.cwd(), "backend");
    this.configuredPackagedCommand =
      !packaged ||
      Boolean(
        opts.backendCommand && opts.backendCwd && validBackendArgs && Array.isArray(rawBackendArgs),
      );
    this.command = packaged ? (opts.backendCommand ?? "") : "uv";
    this.args = packaged
      ? validBackendArgs && Array.isArray(rawBackendArgs)
        ? (rawBackendArgs as string[])
        : []
      : ["run", "python", "-m", "app"];
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
    if (!this.configuredPackagedCommand || this.invalidPackagedArgs) throw new BackendStartError();
    this.token = randomBytes(32).toString("hex");
    const env = {
      ...process.env,
      DOCMIND_SESSION_TOKEN: this.token,
      DOCMIND_DATA_DIR: this.dataDir,
      DOCMIND_PORT: "18900",
    };
    let startError: Error | undefined;
    let exited = false;
    this.childExited = false;
    try {
      this.child = this.spawn(this.command, this.args, {
        cwd: this.cwd,
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
      if (process.env.DOCMIND_E2E === "1" && artifacts)
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
      if (process.env.DOCMIND_E2E === "1" && artifacts)
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
            baseUrl: "http://127.0.0.1:18900",
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
        this.fetchFn("http://127.0.0.1:18900/health", {
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
    const safe = this.stderr
      .replaceAll(this.token, "[redacted]")
      .replace(/DOCMIND_SESSION_TOKEN=[^\s]+/g, "DOCMIND_SESSION_TOKEN=[redacted]")
      .replace(/\/[\w.@+~%=-]+(?:\/[\w.@+~%=-]+)*/g, "[path]");
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
