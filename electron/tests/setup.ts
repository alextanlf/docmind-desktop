import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

// jsdom does not implement scrollIntoView; renderer navigation relies on the browser API.
Element.prototype.scrollIntoView = () => {};

// jsdom has no PointerEvent, so `fireEvent.pointerDown` would otherwise build a
// bare Event with no button/clientX/pointerId. Resizing code that checks
// `event.button !== 0` would then reject every synthetic drag and the tests
// would pass for the wrong reason.
if (typeof globalThis.PointerEvent === "undefined") {
  class JsdomPointerEvent extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    readonly width: number;
    readonly height: number;
    readonly pressure: number;

    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
      this.pointerType = init.pointerType ?? "mouse";
      this.isPrimary = init.isPrimary ?? true;
      this.width = init.width ?? 1;
      this.height = init.height ?? 1;
      this.pressure = init.pressure ?? 0;
    }
  }
  globalThis.PointerEvent = JsdomPointerEvent as unknown as typeof PointerEvent;
}

// Pointer capture is part of the drag path but jsdom does not implement it.
if (typeof Element.prototype.setPointerCapture !== "function") {
  Element.prototype.setPointerCapture = function setPointerCapture() {};
  Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  Element.prototype.hasPointerCapture = function hasPointerCapture() {
    return false;
  };
}

// jsdom implements neither observer, and the PDF reader depends on both:
// ResizeObserver to size a page against the scroll container, IntersectionObserver
// to defer rasterization until a page approaches the viewport.
//
// The stubs below report every observed element as visible and already sized.
// A no-op stub would leave pages as empty placeholders forever, and any
// assertion about rendering would then pass for the wrong reason.
type ObserverCallback = (entries: unknown[], observer: unknown) => void;

function installObserverStubs() {
  const observedClasses: string[] = [];
  class JsdomIntersectionObserver {
    readonly root = null;
    readonly rootMargin = "";
    readonly thresholds: number[] = [];
    constructor(private readonly callback: ObserverCallback) {}
    observe(target: Element) {
      observedClasses.push(target.className);
      queueMicrotask(() =>
        this.callback([{ isIntersecting: true, target }], this),
      );
    }
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  class JsdomResizeObserver {
    constructor(private readonly callback: ObserverCallback) {}
    observe(target: Element) {
      queueMicrotask(() => this.callback([{ target }], this));
    }
    unobserve() {}
    disconnect() {}
  }
  if (typeof globalThis.IntersectionObserver === "undefined") {
    globalThis.IntersectionObserver =
      JsdomIntersectionObserver as unknown as typeof IntersectionObserver;
  }
  if (typeof globalThis.ResizeObserver === "undefined") {
    globalThis.ResizeObserver = JsdomResizeObserver as unknown as typeof ResizeObserver;
  }
  return observedClasses;
}

export const observedByIntersection = installObserverStubs();

// jsdom's getContext logs a "not implemented" error and returns null, so any code
// guarding on the context would silently take the bail-out branch and the canvas
// path would never be exercised. Return a minimal stub instead.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
    return { canvas: this } as unknown as CanvasRenderingContext2D;
  } as unknown as HTMLCanvasElement["getContext"];
}