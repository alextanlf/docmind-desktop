import { randomBytes } from 'node:crypto'
import { spawn as nodeSpawn, ChildProcess } from 'node:child_process'

export interface BackendConnection { baseUrl: string; token: string }
type SpawnFn = (command: string, args: string[], options: { cwd?: string; env?: NodeJS.ProcessEnv; stdio?: unknown }) => ChildProcess
export class BackendStartError extends Error { code = 'BACKEND_START_FAILED'; constructor(message: string){super(message); this.name='BackendStartError'} }

export class BackendManager {
  private child: any; private connection?: BackendConnection; private stderr = ''
  private readonly spawn: SpawnFn; private readonly fetchFn: typeof fetch; private readonly dataDir: string
  private readonly interval: number; private readonly timeout: number; private readonly cwd?: string; private readonly command: string; private readonly args: string[]
  constructor(opts: { spawn?: SpawnFn; fetch?: typeof fetch; dataDir?: string; healthIntervalMs?: number; startupTimeoutMs?: number; cwd?: string; command?: string; args?: string[] }) {
    this.spawn = opts.spawn ?? nodeSpawn as any; this.fetchFn = opts.fetch ?? fetch; this.dataDir = opts.dataDir ?? ''; this.interval = opts.healthIntervalMs ?? 100; this.timeout = opts.startupTimeoutMs ?? 15000; this.cwd = opts.cwd; this.command = opts.command ?? 'uv'; this.args = opts.args ?? ['run','python','-m','app']
  }
  async start(): Promise<BackendConnection> {
    if (this.connection) return this.connection
    const token = randomBytes(32).toString('hex'); const env = { ...process.env, DOCMIND_SESSION_TOKEN: token, DOCMIND_DATA_DIR: this.dataDir, DOCMIND_PORT: '18900' }
    this.child = this.spawn(this.command, this.args, { cwd: this.cwd, env, stdio: ['ignore','ignore','pipe'] }); this.child.stderr?.on?.('data', (d: Buffer|string) => { this.stderr = (this.stderr + String(d)).slice(-2000) })
    let exited = false; this.child.once?.('exit', () => { exited = true })
    const started = Date.now()
    while (Date.now() - started < this.timeout) {
      if (exited) throw new BackendStartError(this.redactedError())
      try { const res = await this.fetchFn('http://127.0.0.1:18900/health', { headers: { 'X-DocMind-Token': token } }); if (res.ok) { this.connection = { baseUrl: 'http://127.0.0.1:18900', token }; return this.connection } } catch { /* retry */ }
      await new Promise(r => setTimeout(r, this.interval))
    }
    throw new BackendStartError(this.redactedError())
  }
  private redactedError() { return `backend failed to start${this.stderr ? `: ${this.stderr.replace(/DOCMIND_SESSION_TOKEN=[^\s]+/g, 'DOCMIND_SESSION_TOKEN=[redacted]')}` : ''}` }
  async request(path: string, init: RequestInit = {}) { if (!this.connection) throw new Error('BACKEND_NOT_STARTED'); const headers = new Headers(init.headers); headers.set('X-DocMind-Token', this.connection.token); return this.fetchFn(`${this.connection.baseUrl}${path}`, { ...init, headers }) }
  async stop() { const child = this.child; this.child = undefined; this.connection = undefined; if (!child || !child.pid) return; try { child.kill('SIGTERM') } catch {} await new Promise<void>(resolve => { let done = false; const finish=()=>{if(!done){done=true;resolve()}}; child.once?.('exit', finish); setTimeout(finish,3000) }) }
}
