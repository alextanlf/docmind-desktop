import { randomBytes } from "node:crypto";
import { spawn as nodeSpawn, ChildProcess } from "node:child_process";
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
  }) {
    this.spawn = opts.spawn ?? (nodeSpawn as SpawnFn);
    this.fetchFn = opts.fetch ?? fetch;
    this.dataDir = opts.dataDir ?? "";
    this.interval = opts.healthIntervalMs ?? 100;
    this.timeout = opts.startupTimeoutMs ?? 15000;
    this.shutdownTimeout = opts.shutdownTimeoutMs ?? 3000;
    const packaged = opts.packaged ?? false;
    this.cwd = packaged
      ? (opts.repoDir ?? process.cwd())
      : join(opts.repoDir ?? process.cwd(), "backend");
    this.configuredPackagedCommand =
      !packaged || Boolean(opts.backendCommand && opts.backendArgs);
    this.command = packaged ? (opts.backendCommand ?? "") : "uv";
    this.args = packaged
      ? (opts.backendArgs ?? ["-m", "app"])
      : ["run", "python", "-m", "app"];
  }
  async start(): Promise<BackendConnection> {
    if (this.connection) return this.connection;
    if (!this.configuredPackagedCommand) throw new BackendStartError();
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
    });
    child.once("error", () => {
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
        throw startError ?? new BackendStartError(diagnostic);
      }
      try {
        const response = await Promise.race([
          this.fetchFn("http://127.0.0.1:18900/health", {
            headers: { "X-DocMind-Token": this.token },
          }),
          new Promise<Response>((_, reject) => {
            const check = setInterval(() => {
              if (startError || exited) {
                clearInterval(check);
                reject(
                  startError ?? new BackendStartError(this.redactedError()),
                );
              }
            }, 10);
          }),
        ]);
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
  private redactedError() {
    const safe = this.stderr
      .replaceAll(this.token, "[redacted]")
      .replace(
        /DOCMIND_SESSION_TOKEN=[^\s]+/g,
        "DOCMIND_SESSION_TOKEN=[redacted]",
      )
      .replace(/\/[\w.@+~%=-]+(?:\/[\w.@+~%=-]+)*/g, "[path]");
    return safe
      ? `Backend failed to start: ${safe}`
      : "Backend failed to start";
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
