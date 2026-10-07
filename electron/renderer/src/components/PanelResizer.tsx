import { clsx } from "clsx";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  MAIN_COLUMN_MIN_WIDTH,
  PANEL_SIZE_LIMITS,
  clampPanelSize,
  usePanelSizeStore,
  type PanelSide,
} from "../stores/panel-size-store";

type PanelResizerProps = {
  /** Which stored panel width this handle drives. */
  side: PanelSide;
  /** The grid the handle lives in; used to work out how much room is left. */
  containerRef: React.RefObject<HTMLElement>;
  /** Rendered width of the *other* side panel, 0 when it is closed. */
  siblingWidth: number;
  onDragStateChange: (dragging: boolean) => void;
};

/**
 * A draggable separator between two columns.
 *
 * The width lives in CSS custom properties on the grid, so this component only
 * has to turn pointer movement into a clamped pixel value. Keyboard resizing and
 * double-click-to-reset come free from the same store.
 */
export function PanelResizer({
  containerRef,
  onDragStateChange,
  siblingWidth,
  side,
}: PanelResizerProps) {
  const size = usePanelSizeStore((state) => state.sizes[side]);
  const setPanelSize = usePanelSizeStore((state) => state.setPanelSize);
  const resetPanelSize = usePanelSizeStore((state) => state.resetPanelSize);
  const [dragging, setDragging] = useState(false);
  const draggingRef = useRef(false);
  const limits = PANEL_SIZE_LIMITS[side];

  const ceiling = useCallback(() => {
    const container = containerRef.current;
    const containerWidth = container?.getBoundingClientRect().width ?? 0;
    if (!containerWidth) return limits.maxSize;
    return containerWidth - siblingWidth - MAIN_COLUMN_MIN_WIDTH;
  }, [containerRef, limits.maxSize, siblingWidth]);

  const applySize = useCallback(
    (next: number) => setPanelSize(side, clampPanelSize(side, next, ceiling())),
    [ceiling, setPanelSize, side],
  );

  const endDrag = useCallback(() => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
    onDragStateChange(false);
  }, [onDragStateChange]);

  useEffect(() => {
    if (!dragging) return;
    // Selecting text mid-drag turns the cursor into an I-beam and the drag dies.
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.userSelect = previousUserSelect;
    };
  }, [dragging]);

  useEffect(() => {
    if (!dragging) return;
    const stop = () => endDrag();
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragging, endDrag]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    draggingRef.current = true;
    setDragging(true);
    onDragStateChange(true);
    const target = event.currentTarget;
    if (typeof target.setPointerCapture === "function" && event.pointerId !== undefined) {
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // Capture is a nicety; the window-level pointerup listener is the safety net.
      }
    }
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    // The sidebar grows rightwards from the left edge, the reference panel
    // grows leftwards from the right edge — one sign difference, same clamp.
    const raw = side === "sidebar" ? event.clientX - rect.left : rect.right - event.clientX;
    applySize(raw);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 32 : 8;
    const grow = side === "sidebar" ? "ArrowRight" : "ArrowLeft";
    const shrink = side === "sidebar" ? "ArrowLeft" : "ArrowRight";
    if (event.key === grow) applySize(size + step);
    else if (event.key === shrink) applySize(size - step);
    else if (event.key === "Home") applySize(limits.minSize);
    else if (event.key === "End") applySize(limits.maxSize);
    else if (event.key === "Enter" || event.key === " ") resetPanelSize(side);
    else return;
    event.preventDefault();
  };

  return (
    <div
      aria-label={side === "sidebar" ? "调整侧边栏宽度" : "调整引用资料宽度"}
      aria-orientation="vertical"
      aria-valuemax={limits.maxSize}
      aria-valuemin={limits.minSize}
      aria-valuenow={size}
      className={clsx("panel-resizer", `panel-resizer-${side}`, dragging && "is-dragging")}
      onDoubleClick={() => resetPanelSize(side)}
      onKeyDown={handleKeyDown}
      onPointerCancel={endDrag}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      role="separator"
      tabIndex={0}
    />
  );
}