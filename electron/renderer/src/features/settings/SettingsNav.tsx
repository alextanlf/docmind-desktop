import { useEffect, useRef, useState } from "react";
import { clsx } from "clsx";
import { SETTINGS_SECTION_ORDER } from "./settings-sections";

const ACTIVE_OFFSET = 24;

function findScrollContainer(element: HTMLElement | null) {
  let node = element?.parentElement ?? null;
  while (node) {
    const { overflowY } = window.getComputedStyle(node);
    if (overflowY === "auto" || overflowY === "scroll") return node;
    node = node.parentElement;
  }
  return null;
}

function prefersReducedMotion() {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function SettingsNav() {
  const navRef = useRef<HTMLElement>(null);
  const [activeId, setActiveId] = useState<string>(SETTINGS_SECTION_ORDER[0].id);

  useEffect(() => {
    const scroller = findScrollContainer(navRef.current);
    const sections = SETTINGS_SECTION_ORDER.map((section) =>
      document.getElementById(section.id),
    ).filter((element): element is HTMLElement => element !== null);
    if (sections.length === 0) return;

    let frame = 0;
    const measure = () => {
      frame = 0;
      const rootTop = scroller ? scroller.getBoundingClientRect().top : 0;
      let next = sections[0].id;
      for (const section of sections) {
        if (section.getBoundingClientRect().top - rootTop <= ACTIVE_OFFSET) next = section.id;
        else break;
      }
      const atBottom = scroller
        ? scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 2
        : false;
      if (atBottom) next = sections[sections.length - 1].id;
      setActiveId((current) => (current === next ? current : next));
    };
    const schedule = () => {
      if (frame !== 0) return;
      frame = window.requestAnimationFrame(measure);
    };

    measure();
    const target: EventTarget = scroller ?? window;
    target.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame !== 0) window.cancelAnimationFrame(frame);
      target.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, []);

  return (
    <nav aria-label="设置分区" className="settings-subnav" ref={navRef}>
      {SETTINGS_SECTION_ORDER.map((section) => {
        const Icon = section.icon;
        const active = section.id === activeId;
        return (
          <button
            aria-current={active ? "true" : undefined}
            className={clsx("settings-subnav-item", active && "is-active")}
            key={section.id}
            onClick={() => {
              setActiveId(section.id);
              const target = document.getElementById(section.id);
              if (!target) return;
              target.focus({ preventScroll: true });
              target.scrollIntoView({
                behavior: prefersReducedMotion() ? "auto" : "smooth",
                block: "start",
              });
            }}
            type="button"
          >
            <Icon aria-hidden="true" size={16} />
            <span>{section.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
