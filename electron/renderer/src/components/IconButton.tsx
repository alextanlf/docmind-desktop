import { clsx } from "clsx";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  label: string;
  icon: ReactNode;
  size?: "small" | "regular";
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, size = "regular", className, type = "button", ...props },
  ref,
) {
  return (
    <button
      aria-label={label}
      className={clsx("icon-button", size === "small" && "icon-button-small", className)}
      ref={ref}
      title={label}
      type={type}
      {...props}
    >
      {icon}
    </button>
  );
});
