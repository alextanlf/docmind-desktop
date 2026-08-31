export const logger = {
  info: (...args: unknown[]) => console.info("[docmind]", ...args.map(redact)),
  error: (...args: unknown[]) =>
    console.error("[docmind]", ...args.map(redact)),
};
function redact(value: unknown) {
  if (typeof value !== "string") return value;
  return value
    .replace(/[a-f0-9]{64}/g, "[redacted]")
    .replace(
      /DOCMIND_SESSION_TOKEN=[^\s]+/g,
      "DOCMIND_SESSION_TOKEN=[redacted]",
    )
    .replace(/\/[\w.@+~%=-]+(?:\/[\w.@+~%=-]+)*/g, "[path]");
}
