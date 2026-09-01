import { AlertCircle, CheckCircle2, CircleDashed, Clock3 } from "lucide-react";
import { clsx } from "clsx";

type StatusTone = "success" | "pending" | "error" | "neutral";

const ICONS = {
  success: CheckCircle2,
  pending: Clock3,
  error: AlertCircle,
  neutral: CircleDashed,
} as const;

export function StatusBadge({ label, tone }: { label: string; tone: StatusTone }) {
  const Icon = ICONS[tone];
  return (
    <span className={clsx("status-badge", `status-${tone}`)}>
      <Icon aria-hidden="true" size={14} />
      {label}
    </span>
  );
}
