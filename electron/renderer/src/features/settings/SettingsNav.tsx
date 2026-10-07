import { clsx } from "clsx";
import { settingsModules } from "./settings-modules";

/**
 * The settings navigation: one entry per registered module.
 *
 * This used to be a scroll-spy — it walked the DOM looking for the section
 * whose top edge had crossed a 24px threshold, listened for `scroll` and
 * `resize`, and re-measured on every animation frame to work out which entry to
 * highlight. All of that existed because the page was one long column that you
 * moved *through*.
 *
 * Now that only one module is shown at a time, the nav is a plain list of
 * buttons over a controlled `activeId`. The measurement code is gone rather than
 * merely bypassed: there is no scroll position left for it to interpret.
 */
export function SettingsNav({
  activeId,
  onSelect,
}: {
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <nav aria-label="设置分区" className="settings-subnav">
      {settingsModules().map((module) => {
        const Icon = module.icon;
        const active = module.id === activeId;
        return (
          <button
            aria-current={active ? "true" : undefined}
            className={clsx("settings-subnav-item", active && "is-active")}
            key={module.id}
            onClick={() => onSelect(module.id)}
            type="button"
          >
            <Icon aria-hidden="true" size={16} />
            <span>{module.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
