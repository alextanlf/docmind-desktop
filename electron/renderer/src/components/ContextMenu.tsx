import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal } from "lucide-react";

export type ContextMenuItem = {
  /** 无障碍名。列在 `role="menu"` 里时读屏只报这一条，所以它要能独立说清做什么。 */
  label: string;
  /** 看到的文字。省略时用 `label`（例如「重命名 项目计划」这种带名字的无障碍名）。 */
  text?: ReactNode;
  icon?: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  /** 破坏性操作（移除/删除）—— 强调色与悬停底色都换一档。 */
  danger?: boolean;
};

type ContextMenuProps = {
  items: ContextMenuItem[];
  /** 无障碍名，说明这份菜单属于谁：「知识库操作」「文档操作」。 */
  label: string;
  /** 用来定位菜单的坐标（右键位置，或触发按钮的位置）。 */
  position: { x: number; y: number };
  onClose: () => void;
  /** 关掉之后焦点回哪去。由触发按钮给：它是这一行的"操作入口"。 */
  restoreFocusTo?: { current: HTMLElement | null };
};

/**
 * 一行操作的小菜单。
 *
 * 为什么不用 `<details>`（知识库范围那个控件用的是它）：那个是常驻在工具栏里的选择器，
 * 而这里要的是"右键任意位置弹出、点别处就消失"。`<details>` 的开关是 `open` 属性，
 * 关要靠再点一次 `<summary>` —— 而右键在行内任意处、Esc、滚动列表都要能关，用 details
 * 反而要绕一圈。
 *
 * 渲染到 `document.body`：侧边栏是 `overflow: auto` 的滚动容器，菜单留在行内会被裁掉。
 */
export function ContextMenu({ items, label, position, onClose, restoreFocusTo }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabledIndexes = items
    .map((item, index) => (item.disabled ? -1 : index))
    .filter((index) => index >= 0);
  const [activeIndex, setActiveIndex] = useState(() => enabledIndexes[0] ?? -1);

  // 贴着视口边缘时往里收，而不是让菜单跑出去或把页面撑出滚动条。
  const [placement, setPlacement] = useState(() => ({
    x: position.x,
    y: position.y,
    flipX: false,
    flipY: false,
  }));
  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const bounds = menu.getBoundingClientRect();
    const flipX = position.x + bounds.width > window.innerWidth - 4;
    const flipY = position.y + bounds.height > window.innerHeight - 4;
    setPlacement({
      x: Math.max(4, flipX ? position.x - bounds.width : position.x),
      y: Math.max(4, flipY ? position.y - bounds.height : position.y),
      flipX,
      flipY,
    });
  }, [position.x, position.y]);

  useEffect(() => {
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not([disabled])')?.focus();
  }, []);

  // 关掉菜单时把焦点还回触发按钮。
  //
  // 放在**卸载的清理函数**里而不是各个关闭路径里：关菜单的那一刻 portal 也在这批 DOM
  // 变更里被移除，而移除一个还带着焦点的子树会把焦点丢给 body（实测）。清理函数跑在
  // portal 已经消失之后，这时再还焦点才留得住。
  useEffect(() => {
    const target = restoreFocusTo?.current ?? null;
    return () => {
      if (target?.isConnected) target.focus();
    };
  }, [restoreFocusTo]);

  // 点别处、滚动、改变窗口大小都关掉：菜单锚在一个随时会动的列表上，
  // 位置一旦对不上就该消失，而不是浮在那里指向错误的一行。
  useEffect(() => {
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("contextmenu", onPointerDown);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("contextmenu", onPointerDown);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  });

  function focusItem(index: number) {
    setActiveIndex(index);
    itemRefs.current[index]?.focus();
  }

  /** 上下键在**可用**项之间绕圈；Home/End 直接跳首尾。 */
  function step(delta: number) {
    if (enabledIndexes.length === 0) return;
    const current = enabledIndexes.indexOf(activeIndex);
    focusItem(enabledIndexes[(current + delta + enabledIndexes.length) % enabledIndexes.length]);
  }

  function onPointerDown(event: Event) {
    if (menuRef.current?.contains(event.target as Node)) return;
    if (event.type === "contextmenu") event.preventDefault();
    onClose();
  }
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") onClose();
  };
  const onResize = () => onClose();

  return createPortal(
    <div
      aria-label={label}
      className="context-menu"
      data-flip-x={placement.flipX || undefined}
      data-flip-y={placement.flipY || undefined}
      onContextMenu={(event) => event.preventDefault()}
      ref={menuRef}
      role="menu"
      style={{ left: placement.x, top: placement.y }}
    >
      {items.map((item, index) => (
        <button
          aria-label={item.label}
          className={item.danger ? "context-menu-item is-danger" : "context-menu-item"}
          disabled={item.disabled}
          key={item.label}
          onClick={() => {
            // 先关菜单再执行：动作可能弹出一个模态对话框，让菜单浮在它上面不像话。
            onClose();
            item.onSelect();
          }}
          onFocus={() => setActiveIndex(index)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              step(1);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              step(-1);
            } else if (event.key === "Home") {
              event.preventDefault();
              if (enabledIndexes.length) focusItem(enabledIndexes[0]);
            } else if (event.key === "End") {
              event.preventDefault();
              if (enabledIndexes.length) focusItem(enabledIndexes[enabledIndexes.length - 1]);
            } else if (event.key === "Tab") {
              onClose();
            }
          }}
          ref={(element) => {
            itemRefs.current[index] = element;
          }}
          role="menuitem"
          // 一律 -1：焦点在这个组件里自己管（打开时落在第一项，上下键移动）。
          // 给当前项 0 会让"焦点移到下一项"的那一刻把上一项的 tabIndex 改成 -1，
          // 浏览器随之把正在聚焦的元素失焦 —— 实测就是这样丢掉焦点的。
          tabIndex={-1}
          type="button"
        >
          {item.icon ? (
            <span aria-hidden="true" className="context-menu-icon">
              {item.icon}
            </span>
          ) : null}
          <span className="context-menu-text">{item.text ?? item.label}</span>
        </button>
      ))}
    </div>,
    document.body,
  );
}

type ContextMenuTriggerProps = {
  items: ContextMenuItem[];
  label: string;
  /** 触发按钮的无障碍名，例如「项目计划的操作」。 */
  triggerLabel: string;
  children: ReactNode;
  className?: string;
};

/**
 * 一个可右键、可点「⋯」、可按 Shift+F10 的操作入口。
 *
 * 三种入口缺一不可：右键是用户要的手感，「⋯」让键盘与触控板用户也够得着（纯右键对键盘
 * 不可达），Shift+F10 是 Windows 上的键盘习惯。三者共用同一份 items。
 *
 * `items` 每次渲染都是新数组，所以不能进任何依赖 —— 菜单开着的期间一行内容不会变，
 * 真变了也是动作已经执行、菜单本来就该关了。
 */
export function ContextMenuTrigger({
  items,
  label,
  triggerLabel,
  children,
  className,
}: ContextMenuTriggerProps) {
  const [position, setPosition] = useState<{ x: number; y: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  function openFrom(target: HTMLElement) {
    const bounds = target.getBoundingClientRect();
    setPosition(
      target.classList.contains("context-menu-trigger")
        ? { x: bounds.right - 4, y: bounds.bottom + 2 }
        : { x: bounds.left + 16, y: bounds.top + 12 },
    );
  }

  return (
    <span
      className={className ? `context-menu-anchor ${className}` : "context-menu-anchor"}
      onContextMenu={(event) => {
        event.preventDefault();
        event.stopPropagation();
        openFrom(event.currentTarget);
      }}
    >
      {children}
      <button
        aria-expanded={position !== null}
        aria-haspopup="menu"
        aria-label={triggerLabel}
        className="tree-icon-button context-menu-trigger"
        onClick={(event) => {
          event.stopPropagation();
          triggerRef.current = event.currentTarget;
          if (position) setPosition(null);
          else openFrom(event.currentTarget);
        }}
        onKeyDown={(event) => {
          if (event.key === "F10" && event.shiftKey) {
            event.preventDefault();
            triggerRef.current = event.currentTarget;
            openFrom(event.currentTarget);
          }
        }}
        ref={triggerRef}
        title={triggerLabel}
        type="button"
      >
        <MoreHorizontal aria-hidden="true" size={15} />
      </button>
      {position ? (
        <ContextMenu
          items={items}
          label={label}
          onClose={() => setPosition(null)}
          position={position}
          restoreFocusTo={triggerRef}
        />
      ) : null}
    </span>
  );
}
