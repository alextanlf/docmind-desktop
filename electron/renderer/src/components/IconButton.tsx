import { clsx } from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  label: string;
  icon: ReactNode;
  size?: "small" | "regular";
};

export function IconButton({
  label,
  icon,
  size = "regular",
  className,
  type = "button",
  ...props
}: IconButtonProps) {
  return (
    <button
      aria-label={label}
      className={clsx("icon-button", size === "small" && "icon-button-small", className)}
      title={label}
      type={type}
      {...props}
    >
      {icon}
    </button>
  );
}
