import type { Citation } from "../../../../shared/contracts";

export type ScopedCitation = { id: string; citation: Citation };

export function citationIdentity(scope: string, citation: Citation) {
  const identity = citation.kind === "memory" ? citation.memoryId : citation.kind === "web" ? citation.resultId : citation.chunkId;
  return `${scope}:${citation.sourceId}:${identity}`;
}
