export const logger = { info: (...args: unknown[]) => console.info('[docmind]', ...args.map(redact)), error: (...args: unknown[]) => console.error('[docmind]', ...args.map(redact)) }
function redact(value: unknown) { return typeof value === 'string' ? value.replace(/[a-f0-9]{64}/g, '[redacted]') : value }
