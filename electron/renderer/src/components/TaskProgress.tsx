type Props = { label: string; progress: number; status?: string | null };
export function TaskProgress({ label, progress, status }: Props) {
  const value = Math.max(0, Math.min(100, progress));
  return <div className="task-progress" aria-label={label} aria-live="polite">
    <div className="task-progress-title"><span>{status ?? label}</span><span>{value}%</span></div>
    <progress aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} value={value} max={100}>{value}%</progress>
  </div>;
}
