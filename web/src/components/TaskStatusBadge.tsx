import { Badge } from "@mantine/core";
import type { TaskStatus } from "../api";

export const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: "排队中",
  running: "进行中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
};

const COLORS: Record<TaskStatus, string> = {
  queued: "gray",
  running: "blue",
  succeeded: "green",
  failed: "red",
  cancelled: "yellow",
};

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return (
    <Badge color={COLORS[status]} variant="light">
      {STATUS_LABELS[status]}
    </Badge>
  );
}
