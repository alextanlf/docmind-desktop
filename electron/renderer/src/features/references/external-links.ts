export function isHttpUrl(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function isYuqueUrl(url: string) {
  try {
    const hostname = new URL(url).hostname;
    return hostname === "yuque.com" || hostname.endsWith(".yuque.com");
  } catch {
    return false;
  }
}

export function openExternalUrl(url: string) {
  if (!isHttpUrl(url)) return false;
  if (!isYuqueUrl(url) && !window.confirm("即将打开外部链接，是否继续？")) return false;
  void window.docmind.shell.openExternal(url);
  return true;
}
