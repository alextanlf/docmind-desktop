import { create } from "zustand";
import type { BatchItem, SourcePreview, SourceRef } from "../../../../shared/contracts";

export type ImportStep = 1 | 2 | 3;
export type DuplicateDecision = "skip" | "update" | null;
export type BatchDecision = "create" | "update" | "attach_remote" | "skip";
type ImportState = {
  step: ImportStep;
  source: SourceRef | null;
  preview: SourcePreview | null;
  repositoryId: string | null;
  duplicateDecision: DuplicateDecision;
  jobId: string | null;
  batchId: string | null;
  batchDecisions: Record<string, Record<string, BatchDecision>>;
  batchItems: Record<string, BatchItem[]>;
  setStep: (step: ImportStep) => void;
  setSource: (source: SourceRef | null) => void;
  setPreview: (preview: SourcePreview | null) => void;
  setRepositoryId: (repositoryId: string | null) => void;
  setDuplicateDecision: (duplicateDecision: DuplicateDecision) => void;
  setJobId: (jobId: string | null) => void;
  setBatchId: (batchId: string | null) => void;
  setBatchDecision: (batchId: string, itemId: string, decision: BatchDecision) => void;
  setBatchItems: (batchId: string, items: BatchItem[]) => void;
  reset: () => void;
};
const initialState = {
  step: 1 as ImportStep,
  source: null,
  preview: null,
  repositoryId: null,
  duplicateDecision: null,
  jobId: null,
  batchId: null,
  batchDecisions: {},
  batchItems: {},
};
export const useImportStore = create<ImportState>((set) => ({
  ...initialState,
  setStep: (step) => set({ step }),
  setSource: (source) => set({ source }),
  setPreview: (preview) => set({ preview }),
  setRepositoryId: (repositoryId) => set({ repositoryId }),
  setDuplicateDecision: (duplicateDecision) => set({ duplicateDecision }),
  setJobId: (jobId) => set({ jobId }),
  setBatchId: (batchId) => set({ batchId }),
  setBatchDecision: (batchId, itemId, decision) =>
    set((state) => ({
      batchDecisions: {
        ...state.batchDecisions,
        [batchId]: { ...(state.batchDecisions[batchId] ?? {}), [itemId]: decision },
      },
    })),
  setBatchItems: (batchId, items) =>
    set((state) => ({ batchItems: { ...state.batchItems, [batchId]: items } })),
  reset: () => set(initialState),
}));
