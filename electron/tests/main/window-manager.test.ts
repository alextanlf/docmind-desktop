import { describe, expect, it } from 'vitest'
import { buildWindowOptions, isAllowedNavigation, handleWindowOpen } from '../../main/window-manager'

describe('window security', () => {
  it('creates sandboxed renderer without node access', () => expect(buildWindowOptions().webPreferences).toMatchObject({ contextIsolation: true, nodeIntegration: false, sandbox: true }))
  it('blocks navigation away from app origin', () => { expect(isAllowedNavigation('http://127.0.0.1:5173', 'https://evil.test')).toBe(false) })
  it('opens only http(s) external URLs', () => { const opened: string[] = []; expect(handleWindowOpen('https://example.com', (u) => opened.push(u))).toBe(false); expect(opened).toEqual(['https://example.com/']); expect(handleWindowOpen('file:///tmp/x', (u) => opened.push(u))).toBe(false); expect(opened).toHaveLength(1) })
})
