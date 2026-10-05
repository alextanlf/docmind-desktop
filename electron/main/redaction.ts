/**
 * Single source of truth for secret redaction in main-process diagnostics.
 *
 * These patterns used to be copy-pasted across backend-manager, ipc-handlers,
 * logger and backend-proxy. A rename of the session-token variable would have
 * silently disabled redaction in every copy at once, and the two path patterns
 * had drifted to different strengths (`[\w.@+~%=-]` vs `[^\s\/]`), so the same
 * absolute path could be masked in one log line and printed in the next.
 */

/** `DOCMIND_SESSION_TOKEN=<anything non-whitespace>` → `DOCMIND_SESSION_TOKEN=[redacted]` */
export const SESSION_TOKEN_PATTERN = /DOCMIND_SESSION_TOKEN=[^\s]+/g;
export const SESSION_TOKEN_REPLACEMENT = "DOCMIND_SESSION_TOKEN=[redacted]";

/**
 * Absolute paths in either POSIX or Windows form.
 *
 * The two historical copies were both weaker: `[\w.@+~%=-]` stopped at the
 * first space, and neither matched the Windows backslash form at all.
 *
 * Matching rules, in order:
 *  - a quoted path is matched whole (this is the form that actually appears in
 *    backend stderr, e.g. `'/Applications/My App/run backend'`), quotes kept;
 *  - a URL keeps its scheme and authority so the failing endpoint stays
 *    identifiable, and only its path component is masked;
 *  - a bare path runs to the next separator. A path whose segments contain
 *    spaces is only masked up to the first space unless it was quoted; that
 *    residue is deliberate — over-greedy matching would swallow the surrounding
 *    sentence and make diagnostics useless. Path segments are not secrets; the
 *    session token is, and that is matched independently of this pattern.
 */
const PATH_SOURCE = new RegExp(
  [
    `(["'])(?:[A-Za-z]:[\\\\/]|/)[^"']*\\1`,
    `([A-Za-z][A-Za-z0-9+.-]*:\\/\\/[^\\s/"']+)[^\\s]*`,
    `(?:[A-Za-z]:[\\\\/]|/)[^\\s"']+`,
  ].join("|"),
  "g",
);

export const PATH_REPLACEMENT = "[path]";

/** Replace matches with `[path]`, preserving quotes and any URL scheme+authority. */
function maskPath(match: string): string {
  const quote = /^["']/.exec(match)?.[0];
  if (quote) return `${quote}${PATH_REPLACEMENT}${quote}`;
  const authority = /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s/]+/.exec(match)?.[0];
  if (authority) return `${authority}${PATH_REPLACEMENT}`;
  return PATH_REPLACEMENT;
}

/** 64-character lowercase hex (session tokens and sha256 digests). */
export const HEX_DIGEST_PATTERN = /[a-f0-9]{64}/g;
export const HEX_DIGEST_REPLACEMENT = "[redacted]";

/**
 * Mask secrets in a diagnostic string.
 *
 * @param value raw text, typically captured stderr or an Error's message+stack
 * @param options.digests also mask bare 64-char hex, which the logger needs for
 *   token/sha256 values that appear without a variable name. Off by default
 *   because it can over-mask unrelated hex strings.
 */
export function redactSecrets(
  value: string,
  options: { digests?: boolean } = {},
): string {
  let output = value
    .replace(SESSION_TOKEN_PATTERN, SESSION_TOKEN_REPLACEMENT)
    .replace(PATH_SOURCE, maskPath);
  if (options.digests) {
    output = output.replace(HEX_DIGEST_PATTERN, HEX_DIGEST_REPLACEMENT);
  }
  return output;
}
