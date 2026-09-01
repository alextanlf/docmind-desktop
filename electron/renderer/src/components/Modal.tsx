import { createPortal } from "react-dom";
import {
  useCallback,
  useEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { clsx } from "clsx";

const FOCUSABLE_SELECTOR = [
  "button:not(:disabled)",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  "a[href]",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusableElements(dialog: HTMLElement | null) {
  return dialog ? Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)) : [];
}

type ModalProps = {
  labelledBy: string;
  children: ReactNode;
  className: string;
  backdropClassName?: string;
  initialFocusRef?: RefObject<HTMLElement>;
  onEscape?: () => void;
};

export function Modal({
  labelledBy,
  children,
  className,
  backdropClassName,
  initialFocusRef,
  onEscape,
}: ModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  const focusInitial = useCallback(() => {
    const target =
      initialFocusRef?.current ?? getFocusableElements(dialogRef.current)[0] ?? dialogRef.current;
    target?.focus();
  }, [initialFocusRef]);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const backdrop = backdropRef.current;
    const background = Array.from(document.body.children).filter((element) => element !== backdrop);
    const previous = background.map((element) => ({
      element,
      ariaHidden: element.getAttribute("aria-hidden"),
      inert: element.hasAttribute("inert"),
    }));

    for (const element of background) {
      element.setAttribute("aria-hidden", "true");
      element.setAttribute("inert", "");
    }
    focusInitial();

    return () => {
      for (const state of previous) {
        if (state.ariaHidden === null) state.element.removeAttribute("aria-hidden");
        else state.element.setAttribute("aria-hidden", state.ariaHidden);
        if (!state.inert) state.element.removeAttribute("inert");
      }
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [focusInitial, labelledBy]);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (onEscape) onEscape();
      else focusInitial();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = getFocusableElements(dialogRef.current);
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialogRef.current?.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !dialogRef.current?.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div
      className={clsx("dialog-backdrop", backdropClassName)}
      data-modal-layer="true"
      ref={backdropRef}
    >
      <section
        aria-labelledby={labelledBy}
        aria-modal="true"
        className={className}
        onKeyDown={handleKeyDown}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        {children}
      </section>
    </div>,
    document.body,
  );
}
