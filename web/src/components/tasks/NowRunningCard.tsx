import { Button, Progress } from "@mantine/core";
import { useEffect, useState } from "react";
import type { TaskRow } from "../../api";
import { IconClose, IconOrganize, IconScan, IconScrape, IconTasks } from "../../icons";
import styles from "../../pages/TasksPage.module.css";

function taskKindIcon(taskType: string) {
  switch (taskType) {
    case "organize":
      return <IconScan size={18} />;
    case "scrape":
    case "scrape_library":
      return <IconScrape size={18} />;
    case "arrange":
    case "arrange_library":
    case "write_files":
    case "rename":
      return <IconOrganize size={18} />;
    default:
      return <IconTasks size={18} />;
  }
}

export function formatElapsed(startedAt: string | null, nowMs: number): string {
  if (startedAt === null) {
    return "";
  }
  const seconds = Math.max(0, Math.floor((nowMs - new Date(startedAt).getTime()) / 1000));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} 分 ${seconds % 60} 秒`;
  }
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

export function NowRunningCard({
  task,
  cancelling,
  onCancel,
}: {
  task: TaskRow;
  cancelling: boolean;
  onCancel: (id: number) => void;
}) {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const succeeded = task.tree_status.succeeded ?? 0;
  const running = task.tree_status.running ?? 0;
  const queued = task.tree_status.queued ?? 0;
  const failed = task.tree_status.failed ?? 0;
  const cancelled = task.tree_status.cancelled ?? 0;

  const treeTotal = succeeded + running + queued + failed + cancelled;
  const treeDone = succeeded + failed + cancelled;

  let progressValue = 100;
  let animated = true;
  if (task.progress !== null && task.progress.total > 0) {
    progressValue = Math.min(100, (task.progress.current / task.progress.total) * 100);
    animated = false;
  } else if (treeTotal > 0) {
    progressValue = Math.min(100, (treeDone / treeTotal) * 100);
    animated = false;
  }

  const elapsed = formatElapsed(task.started_at ?? task.created_at, nowMs);
  const progressMsg = task.progress?.message ? ` · ${task.progress.message}` : "";
  const titleText =
    task.title !== "" ? `${task.type_label}「${task.title}」` : task.type_label;

  return (
    <section className={styles.nowCard} aria-label="正在运行的任务">
      <div className={styles.spinRing} aria-hidden>
        {taskKindIcon(task.task_type)}
      </div>
      <div className={styles.nowBody}>
        <h3 className={styles.nowTitle}>
          <span>{titleText}</span>
          {(elapsed !== "" || progressMsg !== "") && (
            <small className={styles.nowMeta}>
              {elapsed !== "" ? `已运行 ${elapsed}` : ""}
              {progressMsg}
            </small>
          )}
        </h3>
        <div className={styles.nowBar}>
          <Progress value={progressValue} animated={animated} size={5} radius="xl" />
        </div>
        <div className={styles.nowSub}>
          <span className={styles.nowSubItem}>
            <b>{succeeded}</b>完成
          </span>
          <span className={styles.nowSubItem}>
            <b>{running}</b>进行中
          </span>
          <span className={styles.nowSubItem}>
            <b>{queued}</b>排队
          </span>
          <span className={`${styles.nowSubItem} ${failed > 0 ? styles.nowSubBad : ""}`}>
            <b>{failed}</b>失败
          </span>
        </div>
      </div>
      <Button
        size="xs"
        variant="default"
        leftSection={<IconClose size={13} />}
        loading={cancelling}
        onClick={() => onCancel(task.id)}
      >
        取消
      </Button>
    </section>
  );
}
