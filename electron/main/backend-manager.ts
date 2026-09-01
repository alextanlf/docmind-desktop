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
      (Array.isArray(rawBackendArgs) && rawBackendArgs.every((arg) => typeof arg === "string"));
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
  async start(): Promise<BackendConnection> {
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
    });
    const started = Date.now();
    while (Date.now() - started < this.timeout) {
      if (startError || exited) {
        const diagnostic = this.redactedError();
        await this.stop();
        throw new BackendStartError(diagnostic);
      }
      try {
        const response = await this.healthRequest(
          () => startError ?? (exited ? new BackendStartError(this.redactedError()) : undefined),
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
      await new Promise((resolve) => setTimeout(resolve, this.interval));
    }
    const diagnostic = this.redactedError();
    await this.stop();
    throw new BackendStartError(diagnostic);
  }
  private async healthRequest(failure: () => Error | undefined) {
    let rejectChild: (error: Error) => void = () => {};
    const childFailure = new Promise<Response>((_, reject) => {
      rejectChild = reject;
    });
    const check = setInterval(() => {
      const error = failure();
      if (error) rejectChild(error);
    }, 10);
    try {
      return await Promise.race([
        this.fetchFn("http://127.0.0.1:18900/health", {
          headers: { "X-DocMind-Token": this.token },
        }),
        childFailure,
      ]);
    } finally {
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
  async stop() {
    const child = this.child;
    this.reset();
    if (!child?.pid) return;
    const exited = await new Promise<boolean>((resolve) => {
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
      try {
        child.kill("SIGTERM");
      } catch {
        finish(true);
        return;
      }
    });
    if (!exited) {
      try {
        child.kill("SIGKILL");
      } catch {
        /* exact child already gone */
      }
    }
  }
}
