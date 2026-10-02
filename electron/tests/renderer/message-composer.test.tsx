import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageComposer } from "../../renderer/src/features/chat/MessageComposer";

function renderComposer(overrides: { disabled?: boolean; value?: string } = {}) {
  const onSend = vi.fn();
  render(
    <MessageComposer
      disabled={overrides.disabled ?? false}
      onChange={vi.fn()}
      onSend={onSend}
      value={overrides.value ?? "问题"}
    />,
  );
  return { onSend, textarea: screen.getByLabelText("输入问题") };
}

describe("message composer shortcuts", () => {
  it("sends on Enter", () => {
    const { onSend, textarea } = renderComposer();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("keeps Shift + Enter as a newline without sending", () => {
    const { onSend, textarea } = renderComposer();
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send while a composition session is active", () => {
    const { onSend, textarea } = renderComposer();
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });
    fireEvent.keyDown(textarea, { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("ignores Enter when the composer is empty", () => {
    const { onSend, textarea } = renderComposer({ value: "   " });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("ignores Enter while sending is disabled", () => {
    const { onSend, textarea } = renderComposer({ disabled: true });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("advertises the shortcuts in the placeholder", () => {
    renderComposer();
    expect(
      screen.getByPlaceholderText("输入问题，Enter 发送，Shift + Enter 换行"),
    ).toBeInTheDocument();
  });
});
