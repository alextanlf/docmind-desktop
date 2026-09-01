import type { Citation } from "../../../../shared/contracts";

export type ScopedCitation = { id: string; citation: Citation };

export function citationIdentity(scope: string, citation: Citation) {
  return `${scope}:${citation.sourceId}:${citation.chunkId}`;
}
