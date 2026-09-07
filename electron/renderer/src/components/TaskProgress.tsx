type Props = {
  label?: string;
  title?: string;
  progress: number | null | undefined;
  progressText?: string;
  status?: string | null;
  statusText?: string;
  onCancel?: () => void;
  cancelPending?: boolean;
  onRetry?: () => void;
  retryDisabled?: boolean;
};
export function TaskProgress({ label, title = label ?? "任务进度", progress, progressText, status, statusText, onCancel, cancelPending = false, onRetry, retryDisabled = false }: Props) {
  const known = typeof progress === "number" && Number.isInteger(progress) && progress >= 0 && progress <= 100;
  const text = progressText ?? (known ? `${progress}%` : "进行中");
  return <div className="task-progress" aria-label={label ?? title} aria-live="polite">
    <div className="task-progress-title"><strong title={title}>{title}</strong><span>{text}</span></div>
    {known ? <progress aria-label={label ?? title} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress as number} aria-valuetext={text} value={progress as number} max={100}>{text}</progress> : null}
    <p>{cancelPending ? "正在取消" : (statusText ?? status ?? "")}</p>
    {onCancel ? <button type="button" aria-label={`取消${title}`} disabled={cancelPending} onClick={onCancel}>取消</button> : null}
    {onRetry ? <button type="button" aria-label={`重试${title}`} disabled={retryDisabled} onClick={onRetry}>重试</button> : null}
  </div>;
}
