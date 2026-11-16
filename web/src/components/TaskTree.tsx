import { Badge, Button, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useState } from "react";
import {
  type TaskAction,
  type TaskRow,
  fetchTask,
  fetchTaskChildren,
  isTaskBusy,
} from "../api";
import { formatDuration } from "../format";
import { TaskResultPanel } from "./TaskResultPanel";
import { TaskStatusBadge } from "./TaskStatusBadge";

export interface TaskNodeActions {
  onCancel: (id: number) => void;
  onRetry: (id: number) => void;
  onDelete: (id: number) => void;
  pending: boolean;
}

const ACTION_LABELS: Record<TaskAction, string> = { cancel: "取消", retry: "重试", delete: "删除" };

function actionsFor(task: TaskRow): TaskAction[] {
  if (isTaskBusy(task)) {
    return ["cancel"];
  }
  return task.status === "failed" || task.status === "cancelled" ? ["retry", "delete"] : ["delete"];
}

/** 行上的无障碍名字：类型、标题和编号，用来在测试里定位某一行。 */
export function taskRowLabel(task: TaskRow): string {
  return `${task.type_label}${task.title === "" ? "" : ` ${task.title}`} #${task.id}`;
}

function ConfirmDelete({
  task,
  onClose,
  onConfirm,
  pending,
}: {
  task: TaskRow;
  onClose: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <Modal opened onClose={onClose} title="删除任务">
      <Stack>
        <Text size="sm">
          删除任务 #{task.id}「{task.type_label}
          {task.title === "" ? "" : ` ${task.title}`}」？记录和日志会一起删掉，硬盘上的文件不动。
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button color="red" loading={pending} onClick={onConfirm}>
            确认删除
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function SubtreeBadges({ task, depth }: { task: TaskRow; depth: number }) {
  // 只在根任务上显示整棵子孙里的失败和进行中。
  if (depth > 0) {
    return null;
  }
  const failed = task.tree_status.failed ?? 0;
  const running = task.tree_status.running ?? 0;
  const queued = task.tree_status.queued ?? 0;
  if (failed === 0 && running === 0 && queued === 0) {
    return null;
  }
  return (
    <Group gap={4} wrap="nowrap">
      {failed > 0 && (
        <Badge size="xs" variant="light" color="red">
          {failed} 失败
        </Badge>
      )}
      {running > 0 && (
        <Badge size="xs" variant="light" color="blue">
          {running} 进行中
        </Badge>
      )}
      {queued > 0 && (
        <Badge size="xs" variant="light" color="gray">
          {queued} 排队
        </Badge>
      )}
    </Group>
  );
}

function NodeRow({
  task,
  depth,
  open,
  onToggle,
  actions,
  onDelete,
}: {
  task: TaskRow;
  depth: number;
  open: boolean;
  onToggle: () => void;
  actions: TaskNodeActions;
  onDelete: () => void;
}) {
  const duration = formatDuration(task.started_at, task.finished_at);
  const buttons = actionsFor(task);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onToggle();
    }
  }

  return (
    <Group
      gap="xs"
      wrap="nowrap"
      align="center"
      role="button"
      tabIndex={0}
      aria-label={taskRowLabel(task)}
      aria-expanded={open}
      style={{ cursor: "pointer", padding: "4px 0" }}
      onClick={onToggle}
      onKeyDown={onKeyDown}
    >
      <Text size="xs" c="dimmed" aria-hidden style={{ width: "0.75rem", flexShrink: 0 }}>
        {open ? "▾" : "▸"}
      </Text>
      <div style={{ flexShrink: 0 }}>
        <TaskStatusBadge status={task.status} />
      </div>
      <Text size="sm" fw={500} style={{ flexShrink: 0 }}>
        {task.type_label}
      </Text>
      {task.title !== "" && (
        <Text size="sm" truncate style={{ minWidth: 0 }}>
          {task.title}
        </Text>
      )}
      {task.child_count > 0 && (
        <Badge size="xs" variant="light" color="gray" tt="none" style={{ flexShrink: 0 }}>
          {task.child_count} 个子任务
        </Badge>
      )}
      <SubtreeBadges task={task} depth={depth} />
      {task.error !== null && (
        <Text size="xs" c="red" truncate title={task.error} style={{ minWidth: 0, flex: 1 }}>
          {task.error}
        </Text>
      )}
      <div style={{ flex: 1 }} />
      {duration !== "" && (
        <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
          {duration}
        </Text>
      )}
      <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
        #{task.id}
      </Text>
      <Group
        gap={2}
        wrap="nowrap"
        style={{ flexShrink: 0 }}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
      >
        {buttons.map((action) => (
          <Button
            key={action}
            size="compact-xs"
            variant="subtle"
            color={action === "delete" ? "red" : undefined}
            disabled={actions.pending}
            onClick={() => {
              if (action === "cancel") {
                actions.onCancel(task.id);
              } else if (action === "retry") {
                actions.onRetry(task.id);
              } else {
                onDelete();
              }
            }}
          >
            {ACTION_LABELS[action]}
          </Button>
        ))}
      </Group>
    </Group>
  );
}

function TaskNode({
  task,
  depth,
  opened,
  onToggle,
  actions,
}: {
  task: TaskRow;
  depth: number;
  opened: ReadonlySet<number>;
  onToggle: (id: number) => void;
  actions: TaskNodeActions;
}) {
  const [confirming, setConfirming] = useState(false);
  const open = opened.has(task.id);
  const busy = isTaskBusy(task);
  const detail = useQuery({
    queryKey: ["task-detail", task.id],
    queryFn: () => fetchTask(task.id),
    enabled: open,
    refetchInterval: busy ? 2000 : false,
  });
  const children = useQuery({
    queryKey: ["task-children", task.id],
    queryFn: () => fetchTaskChildren(task.id),
    enabled: open && task.child_count > 0,
    refetchInterval: busy ? 2000 : false,
  });

  return (
    <div>
      <NodeRow
        task={task}
        depth={depth}
        open={open}
        onToggle={() => onToggle(task.id)}
        actions={actions}
        onDelete={() => setConfirming(true)}
      />
      {confirming && (
        <ConfirmDelete
          task={task}
          pending={actions.pending}
          onClose={() => setConfirming(false)}
          onConfirm={() => {
            actions.onDelete(task.id);
            setConfirming(false);
          }}
        />
      )}
      {open && (
        <div
          style={{
            marginLeft: 8,
            paddingLeft: 12,
            borderLeft: "1px solid var(--mantine-color-default-border)",
          }}
        >
          {detail.isPending && <Loader size="xs" />}
          {detail.isError && (
            <Text size="sm" c="red">
              读不到任务详情：{detail.error.message}
            </Text>
          )}
          {detail.isSuccess && <TaskResultPanel detail={detail.data} />}
          {task.child_count > 0 && (
            <Stack gap={0} mt="sm">
              <Text size="xs" c="dimmed">
                子任务 {task.child_count} 个
              </Text>
              {children.isPending && <Loader size="xs" />}
              {children.isError && (
                <Text size="sm" c="red">
                  读不到子任务：{children.error.message}
                </Text>
              )}
              {children.data?.map((child) => (
                <TaskNode
                  key={child.id}
                  task={child}
                  depth={depth + 1}
                  opened={opened}
                  onToggle={onToggle}
                  actions={actions}
                />
              ))}
            </Stack>
          )}
        </div>
      )}
    </div>
  );
}

/** 任务树：每个任务一行，点开看详情和自己的子任务，逐层往下。 */
export function TaskTree({
  tasks,
  opened,
  onToggle,
  actions,
}: {
  tasks: readonly TaskRow[];
  opened: ReadonlySet<number>;
  onToggle: (id: number) => void;
  actions: TaskNodeActions;
}) {
  return (
    <Stack gap={2}>
      {tasks.map((task) => (
        <TaskNode
          key={task.id}
          task={task}
          depth={0}
          opened={opened}
          onToggle={onToggle}
          actions={actions}
        />
      ))}
    </Stack>
  );
}
