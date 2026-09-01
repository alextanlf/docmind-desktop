import { create } from "zustand";
import type { SourcePreview, SourceRef } from "../../../../shared/contracts";

export type ImportStep = 1 | 2 | 3;
export type DuplicateDecision = "skip" | "update" | null;
type ImportState = {
  step: ImportStep;
  source: SourceRef | null;
  preview: SourcePreview | null;
  repositoryId: string | null;
  duplicateDecision: DuplicateDecision;
  jobId: string | null;
  setStep: (step: ImportStep) => void;
  setSource: (source: SourceRef | null) => void;
  setPreview: (preview: SourcePreview | null) => void;
  setRepositoryId: (repositoryId: string | null) => void;
  setDuplicateDecision: (duplicateDecision: DuplicateDecision) => void;
  setJobId: (jobId: string | null) => void;
  reset: () => void;
};
const initialState = {
  step: 1 as ImportStep,
  source: null,
  preview: null,
  repositoryId: null,
  duplicateDecision: null,
  jobId: null,
};
export const useImportStore = create<ImportState>((set) => ({
  ...initialState,
  setStep: (step) => set({ step }),
  setSource: (source) => set({ source }),
  setPreview: (preview) => set({ preview }),
  setRepositoryId: (repositoryId) => set({ repositoryId }),
  setDuplicateDecision: (duplicateDecision) => set({ duplicateDecision }),
  setJobId: (jobId) => set({ jobId }),
  reset: () => set(initialState),
}));
