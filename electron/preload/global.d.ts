import type { DocMindApi } from "../shared/contracts";

declare global {
  interface Window {
    readonly docmind: DocMindApi;
  }
}

export {};
