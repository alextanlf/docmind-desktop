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