import type { TaskStatus } from "../api";
import styles from "../pages/TasksPage.module.css";
import { Pill, type Tone } from "./ui";

export const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: "排队中",
  running: "进行中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
};

const PILL_TONES: Record<TaskStatus, Tone> = {
  queued: "neutral",
  running: "accent",
  succeeded: "ok",
  failed: "bad",
  cancelled: "neutral",
};

export function TaskStatusBadge({
  status,
  variant = "pill",
}: {
  status: TaskStatus;
  variant?: "dot" | "pill";
}) {
  const label = STATUS_LABELS[status];
  if (variant === "dot") {
    return (
      <span
        className={styles.statusDot}
        data-status={status}
        aria-label={label}
        title={label}
      />
    );
  }
  return (
    <Pill tone={PILL_TONES[status]}>
      <span className={styles.statusDot} data-status={status} aria-hidden />
      {label}
    </Pill>
  );
}
