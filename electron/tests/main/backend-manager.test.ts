import { describe, expect, it, vi } from 'vitest'
import { BackendManager } from '../../main/backend-manager'

function fakeProcess() {
  const listeners: Record<string, (...args: unknown[]) => void> = {}
  return { pid: 42, stderr: { on: vi.fn() }, on: vi.fn((e: string, cb: (...a: unknown[]) => void) => (listeners[e] = cb)), kill: vi.fn(), once: vi.fn(), __exit: () => listeners.exit?.(1) }
}

describe('BackendManager', () => {
  it('passes a random runtime token and waits for authenticated health', async () => {
    const process = fakeProcess()
    const fetch = vi.fn().mockResolvedValue(new Response('{"status":"ok","version":"0.1.0"}'))
    const manager = new BackendManager({ spawn: () => process as any, fetch, dataDir: '/tmp/docmind', healthIntervalMs: 0 })
    const connection = await manager.start()
    expect(connection.token).toMatch(/^[a-f0-9]{64}$/)
    expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:18900/health', expect.objectContaining({ headers: { 'X-DocMind-Token': connection.token } }))
  })

  it('throws start failure when child exits', async () => {
    const process = fakeProcess()
    const manager = new BackendManager({ spawn: () => process as any, fetch: vi.fn().mockRejectedValue(new Error('down')), healthIntervalMs: 0, startupTimeoutMs: 20 })
    setTimeout(() => process.__exit(), 1)
    await expect(manager.start()).rejects.toMatchObject({ code: 'BACKEND_START_FAILED' })
  })

  it('terminates exact child on stop', async () => {
    const process = fakeProcess()
    const manager = new BackendManager({ spawn: () => process as any, fetch: vi.fn().mockResolvedValue(new Response('{}')), healthIntervalMs: 0 })
    await manager.start(); await manager.stop(); expect(process.kill).toHaveBeenCalledWith('SIGTERM')
  })
})
