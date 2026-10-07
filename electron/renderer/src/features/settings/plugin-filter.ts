/**
 * Client-side plugin filtering, split out of the component file.
 *
 * It lives alone because it is pure logic worth testing directly, and because
 * `react-refresh/only-export-components` (a warning, but lint runs with
 * `--max-warnings=0`) rejects a module that exports both a component and a
 * plain function.
 */
import type { PluginManifest } from "../../../../shared/contracts";

/**
 * Free-text filter over each plugin's own declared text.
 *
 * The haystack is exactly what the backend searches (label, provider label,
 * summary, id, keywords), so typing here and typing in the backend's `q`
 * endpoint agree — a card that `GET /api/plugins?q=令牌` returns must not be
 * one this hides. AND semantics: every term must match, so extra words narrow.
 */
export function filterPlugins(plugins: PluginManifest[], query: string): PluginManifest[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return plugins;
  return plugins.filter((plugin) => {
    const haystack = [
      plugin.label,
      plugin.providerLabel,
      plugin.summary ?? "",
      plugin.id,
      ...plugin.keywords,
    ]
      .join(" ")
      .toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}