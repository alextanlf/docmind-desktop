import { redactSecrets } from "./redaction";

export const logger = {
  info: (...args: unknown[]) => console.info("[docmind]", ...args.map(redact)),
  error: (...args: unknown[]) => console.error("[docmind]", ...args.map(redact)),
};
function redact(value: unknown) {
  if (value instanceof Error) {
    return redact(`${value.name}: ${value.message}\n${value.stack ?? ""}`);
  }
  if (typeof value !== "string") return value;
  return redactSecrets(value, { digests: true });
}
