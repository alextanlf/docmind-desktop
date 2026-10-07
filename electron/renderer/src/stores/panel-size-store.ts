import { create } from "zustand";

/**
 * The conversation column must never be squeezed out of existence, so both
 * side panels are capped by "whatever is left after the main column keeps its
 * minimum". Without this cap a wide drag silently clips the chat area because
 * the grid container hides its overflow.
 */
export const MAIN_COLUMN_MIN_WIDTH = 480;

export const PANEL_SIZE_LIMITS = {
  sidebar: { defaultSize: 248, maxSize: 420, minSize: 200 },
  reference: { defaultSize: 320, maxSize: 560, minSize: 240 },
} as const;

export type PanelSide = keyof typeof PANEL_SIZE_LIMITS;

export type PanelSizeLimits = (typeof PANEL_SIZE_LIMITS)[PanelSide];

const STORAGE_KEY = "docmind.panel-sizes";

type StoredSizes = Partial<Record<PanelSide, unknown>>;

/**
 * Clamp a requested width. `ceiling` is what is physically left for this panel
 * once the main column keeps its minimum; it can drop below `minSize` on a very
 * narrow window, and in that case the minimum wins because a usable panel beats
 * a missing one.
 */
export function clampPanelSize(side: PanelSide, size: number, ceiling: number): number {
  const { maxSize, minSize } = PANEL_SIZE_LIMITS[side];
  const upperBound = Math.max(minSize, Math.min(maxSize, Math.ceil(ceiling)));
  if (!Number.isFinite(size)) return PANEL_SIZE_LIMITS[side].defaultSize;
  return Math.min(upperBound, Math.max(minSize, Math.round(size)));
}

function sanitize(side: PanelSide, value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return PANEL_SIZE_LIMITS[side].defaultSize;
  }
  return clampPanelSize(side, value, PANEL_SIZE_LIMITS[side].maxSize);
}

/**
 * Turn persisted JSON back into usable sizes. Anything unreadable falls back to
 * the defaults: a bad value must never reach the grid as `width: NaN`.
 */
export function parseStoredSizes(raw: string | null): Record<PanelSide, number> {
  const fallback = {
    sidebar: PANEL_SIZE_LIMITS.sidebar.defaultSize,
    reference: PANEL_SIZE_LIMITS.reference.defaultSize,
  };
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw) as StoredSizes;
    if (typeof parsed !== "object" || parsed === null) return fallback;
    return {
      sidebar: sanitize("sidebar", parsed.sidebar),
      reference: sanitize("reference", parsed.reference),
    };
  } catch {
    return fallback;
  }
}

function readStoredSizes(): Record<PanelSide, number> {
  if (typeof window === "undefined") {
    return {
      sidebar: PANEL_SIZE_LIMITS.sidebar.defaultSize,
      reference: PANEL_SIZE_LIMITS.reference.defaultSize,
    };
  }
  try {
    return parseStoredSizes(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return {
      sidebar: PANEL_SIZE_LIMITS.sidebar.defaultSize,
      reference: PANEL_SIZE_LIMITS.reference.defaultSize,
    };
  }
}

function persist(sizes: Record<PanelSide, number>) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ reference: sizes.reference, sidebar: sizes.sidebar }),
    );
  } catch {
    // A full or blocked storage must never break resizing.
  }
}

type PanelSizeState = {
  sizes: Record<PanelSide, number>;
  setPanelSize: (side: PanelSide, size: number) => void;
  resetPanelSize: (side: PanelSide) => void;
};

export const usePanelSizeStore = create<PanelSizeState>((set) => ({
  sizes: readStoredSizes(),
  setPanelSize: (side, size) =>
    set((state) => {
      if (state.sizes[side] === size) return state;
      const sizes = { ...state.sizes, [side]: size };
      persist(sizes);
      return { sizes };
    }),
  resetPanelSize: (side) =>
    set((state) => {
      const sizes = { ...state.sizes, [side]: PANEL_SIZE_LIMITS[side].defaultSize };
      persist(sizes);
      return { sizes };
    }),
}));