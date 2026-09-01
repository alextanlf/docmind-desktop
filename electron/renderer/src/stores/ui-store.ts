import { create } from "zustand";

export type ActiveView = "workspace" | "settings";

type UiState = {
  activeView: ActiveView;
  sidebarCollapsed: boolean;
  referencePanelOpen: boolean;
  activeCitationId: string | null;
  activeCitationTrigger: HTMLButtonElement | null;
  setActiveView: (view: ActiveView) => void;
  toggleSidebar: () => void;
  setReferencePanelOpen: (open: boolean) => void;
  setActiveCitationId: (citationId: string | null) => void;
  setActiveCitationTrigger: (trigger: HTMLButtonElement | null) => void;
};

export const useUiStore = create<UiState>((set) => ({
  activeView: "workspace",
  sidebarCollapsed: false,
  referencePanelOpen: true,
  activeCitationId: null,
  activeCitationTrigger: null,
  setActiveView: (activeView) => set({ activeView }),
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setReferencePanelOpen: (referencePanelOpen) => set({ referencePanelOpen }),
  setActiveCitationId: (activeCitationId) => set({ activeCitationId }),
  setActiveCitationTrigger: (activeCitationTrigger) => set({ activeCitationTrigger }),
}));
