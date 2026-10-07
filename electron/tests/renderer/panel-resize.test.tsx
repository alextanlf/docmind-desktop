import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../renderer/src/app/AppProviders";
import { Workspace } from "../../renderer/src/app/Workspace";
import {
  MAIN_COLUMN_MIN_WIDTH,
  PANEL_SIZE_LIMITS,
  parseStoredSizes,
  usePanelSizeStore,
} from "../../renderer/src/stores/panel-size-store";
import { useUiStore } from "../../renderer/src/stores/ui-store";
import { installDocMindApi, session } from "./test-docmind-api";

const VIEWPORT_WIDTH = 1440;

function localStorageKey() {
  return window.localStorage.getItem("docmind.panel-sizes");
}

function stubViewport(width: number) {
  // jsdom reports a zero-width layout, which would silently disable the clamp
  // ceiling and make every drag assertion meaningless.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    bottom: 800,
    height: 800,
    left: 0,
    right: width,
    top: 0,
    width,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

function renderWorkspace() {
  return render(
    <AppProviders>
      <Workspace />
    </AppProviders>,
  );
}

async function renderWithSession() {
  const view = renderWorkspace();
  fireEvent.click(await screen.findByRole("button", { name: session.title }));
  return view;
}

describe("resizable side panels", () => {
  beforeEach(() => {
    window.localStorage.clear();
    installDocMindApi();
    stubViewport(VIEWPORT_WIDTH);
    usePanelSizeStore.setState({
      sizes: {
        reference: PANEL_SIZE_LIMITS.reference.defaultSize,
        sidebar: PANEL_SIZE_LIMITS.sidebar.defaultSize,
      },
    });
    useUiStore.setState({
      activeView: "workspace",
      sidebarCollapsed: false,
      referencePanelOpen: true,
      activeCitationId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("resizes the sidebar by dragging its right edge", async () => {
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.pointerDown(handle, { button: 0, clientX: 248, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 360, pointerId: 1 });

    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe("360px");
  });

  it("resizes the reference panel by dragging its left edge", async () => {
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整引用资料宽度" });

    fireEvent.pointerDown(handle, { button: 0, clientX: 1120, pointerId: 1 });
    // The right panel grows leftwards, so pulling left must widen it.
    fireEvent.pointerMove(handle, { clientX: 1000, pointerId: 1 });

    expect(screen.getByLabelText("工作台").style.getPropertyValue("--reference-width")).toBe(
      "440px",
    );
  });

  it("clamps a sidebar drag so the conversation column keeps its minimum", async () => {
    await renderWithSession();
    const sidebar = screen.getByRole("separator", { name: "调整侧边栏宽度" });
    // Widen the reference panel first: with the 1440px viewport the raw ceiling
    // (1440 - 320 - 480 = 640) sits above the sidebar max, so it would never be
    // the binding constraint. Only a wide sibling proves the ceiling is applied.
    const reference = screen.getByRole("separator", { name: "调整引用资料宽度" });
    fireEvent.pointerDown(reference, { button: 0, clientX: 1120, pointerId: 1 });
    fireEvent.pointerMove(reference, { clientX: 1000, pointerId: 1 });
    fireEvent.pointerUp(reference, { pointerId: 1 });
    const referenceWidth = Number.parseInt(
      screen.getByLabelText("工作台").style.getPropertyValue("--reference-width"),
      10,
    );
    expect(referenceWidth).toBe(440);

    fireEvent.pointerDown(sidebar, { button: 0, clientX: 248, pointerId: 2 });
    fireEvent.pointerMove(sidebar, { clientX: VIEWPORT_WIDTH, pointerId: 2 });

    const expected = VIEWPORT_WIDTH - referenceWidth - MAIN_COLUMN_MIN_WIDTH;
    expect(expected).toBe(520);
    // 520 clears the 420 max, so the ceiling is what has to stop the drag.
    expect(expected).toBeGreaterThan(PANEL_SIZE_LIMITS.sidebar.maxSize);
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.maxSize}px`,
    );
  });

  it("caps the sidebar below its own max when the window leaves no room", async () => {
    stubViewport(1000);
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.pointerDown(handle, { button: 0, clientX: 248, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 1000, pointerId: 1 });

    // 1000 - 320 - 480 = 200, exactly the minimum: a third limit, below max.
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.minSize}px`,
    );
  });

  it("respects the per-panel minimum and maximum", async () => {
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.pointerDown(handle, { button: 0, clientX: 248, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 0, pointerId: 1 });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.minSize}px`,
    );

    fireEvent.pointerMove(handle, { clientX: 5000, pointerId: 1 });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.maxSize}px`,
    );
  });

  it("stops resizing once the pointer is released", async () => {
    await renderWithSession();
    const workspace = screen.getByLabelText("工作台");
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.pointerDown(handle, { button: 0, clientX: 248, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 300, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    // A stray move after release must not keep resizing; that is the classic
    // "panel keeps following the cursor" bug.
    fireEvent.pointerMove(handle, { clientX: 420, pointerId: 1 });

    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe("300px");
  });

  it("ignores non-primary mouse buttons", async () => {
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.pointerDown(handle, { button: 2, clientX: 248, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 400, pointerId: 1 });

    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe("248px");
  });

  it("exposes the current width to assistive tech and resizes from the keyboard", async () => {
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    expect(handle).toHaveAttribute("aria-orientation", "vertical");
    expect(handle).toHaveAttribute("aria-valuenow", String(PANEL_SIZE_LIMITS.sidebar.defaultSize));

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe("256px");

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe("248px");

    fireEvent.keyDown(handle, { key: "Home" });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.minSize}px`,
    );

    fireEvent.keyDown(handle, { key: "End" });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.maxSize}px`,
    );
  });

  it("grows the right panel on ArrowLeft, the mirror of the left panel", async () => {
    await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整引用资料宽度" });

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(screen.getByLabelText("工作台").style.getPropertyValue("--reference-width")).toBe(
      "328px",
    );
  });

  it("restores the default width on Enter and on double click", async () => {
    await renderWithSession();
    const workspace = screen.getByLabelText("工作台");
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe("264px");

    fireEvent.keyDown(handle, { key: "Enter" });
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.defaultSize}px`,
    );

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.doubleClick(handle);
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.defaultSize}px`,
    );
  });

  it("removes the width transition only while dragging", async () => {
    await renderWithSession();
    const workspace = screen.getByLabelText("工作台");
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    expect(workspace).not.toHaveClass("is-resizing");
    fireEvent.pointerDown(handle, { button: 0, clientX: 248, pointerId: 1 });
    expect(workspace).toHaveClass("is-resizing");
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(workspace).not.toHaveClass("is-resizing");
  });

  it("hides the resizers that have no column to resize", async () => {
    renderWorkspace();

    // Empty home view: the reference panel is closed, so only the sidebar handle.
    expect(screen.getByRole("separator", { name: "调整侧边栏宽度" })).toBeInTheDocument();
    expect(screen.queryByRole("separator", { name: "调整引用资料宽度" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "收起侧边栏" }));
    expect(screen.queryByRole("separator", { name: "调整侧边栏宽度" })).not.toBeInTheDocument();
  });

  it("pins the collapsed rail to 64px instead of the remembered width", async () => {
    await renderWithSession();
    const workspace = screen.getByLabelText("工作台");
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });

    fireEvent.pointerDown(handle, { button: 0, clientX: 248, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 400, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe("400px");

    fireEvent.click(screen.getByRole("button", { name: "收起侧边栏" }));
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe("64px");

    // Expanding restores what the user actually chose, not the default.
    fireEvent.click(screen.getByRole("button", { name: "展开侧边栏" }));
    expect(workspace.style.getPropertyValue("--sidebar-width")).toBe("400px");
  });

  it("removes both resizers when the viewport forces the icon rail", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: true,
        media: "(max-width: 1000px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    );
    renderWorkspace();

    expect(screen.queryByRole("separator")).not.toBeInTheDocument();
  });

  it("remembers the widths across a remount", async () => {
    const first = await renderWithSession();
    const handle = screen.getByRole("separator", { name: "调整侧边栏宽度" });
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.keyDown(handle, { key: "ArrowRight" });

    first.unmount();
    // Re-read persisted state the way a fresh launch does.
    usePanelSizeStore.setState({ sizes: parseStoredSizes(localStorageKey()) });
    renderWorkspace();
    await screen.findByRole("button", { name: session.title });

    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe("264px");
  });

  it("discards a corrupted stored width instead of rendering a broken layout", () => {
    localStorage.setItem(
      "docmind.panel-sizes",
      JSON.stringify({ reference: "wide", sidebar: null }),
    );
    usePanelSizeStore.setState({ sizes: parseStoredSizes(localStorage.getItem("docmind.panel-sizes")) });

    renderWorkspace();

    expect(screen.getByLabelText("工作台").style.getPropertyValue("--sidebar-width")).toBe(
      `${PANEL_SIZE_LIMITS.sidebar.defaultSize}px`,
    );
  });

  it("clamps a stored width that is out of range", () => {
    expect(parseStoredSizes(JSON.stringify({ sidebar: 9999 })).sidebar).toBe(
      PANEL_SIZE_LIMITS.sidebar.maxSize,
    );
    expect(parseStoredSizes(JSON.stringify({ sidebar: 2 })).sidebar).toBe(
      PANEL_SIZE_LIMITS.sidebar.minSize,
    );
    expect(parseStoredSizes("not json")).toEqual({
      reference: PANEL_SIZE_LIMITS.reference.defaultSize,
      sidebar: PANEL_SIZE_LIMITS.sidebar.defaultSize,
    });
  });
});