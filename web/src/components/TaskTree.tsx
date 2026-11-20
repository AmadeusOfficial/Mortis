import { Button, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type CSSProperties, type KeyboardEvent, useMemo, useState } from "react";
import { Link } from "react-router";
import {
  type Library,
  type TaskAction,
  type TaskDetail,
  type TaskRow,
  fetchLibraries,
  fetchTask,
  fetchTaskChildren,
  isTaskBusy,
} from "../api";
import { formatDuration } from "../format";
import { IconChevronDown, IconChevronRight } from "../icons";
import styles from "../pages/TasksPage.module.css";
import { TaskResultPanel } from "./TaskResultPanel";
import { TaskStatusBadge } from "./TaskStatusBadge";
import { formatElapsed } from "./tasks/NowRunningCard";

export interface TaskNodeActions {
  onCancel: (id: number) => void;
  onRetry: (id: number) => void;
  onDelete: (id: number) => void;
  pending: boolean;
}

const ACTION_LABELS: Record<TaskAction, string> = { cancel: "取消", retry: "重试", delete: "删除" };
const LIBRARY_TASK_TYPES = new Set(["organize", "scrape_library", "arrange_library"]);

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

function CountPills({ task, depth }: { task: TaskRow; depth: number }) {
  const showSubtree = task.child_count > 0;
  const failed = showSubtree ? (task.tree_status.failed ?? 0) : 0;
  const running = showSubtree ? (task.tree_status.running ?? 0) : 0;
  const queued = showSubtree ? (task.tree_status.queued ?? 0) : 0;
  const selfFailed = depth > 0 && task.status === "failed";
  const selfCancelled = depth > 0 && task.status === "cancelled";

  if (
    task.child_count === 0 &&
    failed === 0 &&
    running === 0 &&
    queued === 0 &&
    !selfFailed &&
    !selfCancelled
  ) {
    return null;
  }

  return (
    <div className={styles.rowCounts}>
      {task.child_count > 0 && <span className={styles.countChip}>{task.child_count} 子任务</span>}
      {running > 0 && (
        <span className={styles.countChip} data-tone="accent">
          {running} 进行中
        </span>
      )}
      {queued > 0 && <span className={styles.countChip}>{queued} 排队</span>}
      {failed > 0 && (
        <span className={styles.countChip} data-tone="bad">
          {failed} 失败
        </span>
      )}
      {selfFailed && (
        <span className={styles.countChip} data-tone="bad">
          失败
        </span>
      )}
      {selfCancelled && <span className={styles.countChip}>已取消</span>}
    </div>
  );
}

function resolveTargetHref(
  task: TaskRow,
  librariesByName: Map<string, Library>,
  cachedDetail: TaskDetail | undefined,
): string | null {
  if (task.title === "") {
    return null;
  }
  if (LIBRARY_TASK_TYPES.has(task.task_type)) {
    const library = librariesByName.get(task.title);
    return library !== undefined ? `/libraries/${library.id}` : null;
  }
  if (cachedDetail?.item !== null && cachedDetail?.item !== undefined) {
    return `/items/${cachedDetail.item.id}`;
  }
  return null;
}

function rowDurationText(task: TaskRow, depth: number): string {
  const finished = formatDuration(task.started_at, task.finished_at);
  if (finished !== "") {
    return finished;
  }
  if (task.status === "running") {
    if (depth === 0 && task.started_at !== null) {
      return formatElapsed(task.started_at, Date.now());
    }
    return "进行中";
  }
  if (task.status === "queued") {
    return "排队";
  }
  return "";
}

function NodeRow({
  task,
  depth,
  open,
  onToggle,
  actions,
  onDelete,
  targetHref,
}: {
  task: TaskRow;
  depth: number;
  open: boolean;
  onToggle: () => void;
  actions: TaskNodeActions;
  onDelete: () => void;
  targetHref: string | null;
}) {
  const duration = rowDurationText(task, depth);
  const buttons = actionsFor(task);
  const rowStyle = { "--depth": depth } as CSSProperties;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onToggle();
    }
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={taskRowLabel(task)}
      aria-expanded={open}
      className={styles.taskRow}
      data-depth={Math.min(depth, 4)}
      data-open={open}
      style={rowStyle}
      onClick={onToggle}
      onKeyDown={onKeyDown}
    >
      <span className={styles.rowChevron} aria-hidden>
        {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
      </span>
      <span className={styles.rowDotWrap}>
        <TaskStatusBadge status={task.status} variant="dot" />
      </span>
      <div className={styles.rowMain}>
        <span className={styles.rowKind}>{task.type_label}</span>
        {task.title !== "" &&
          (targetHref !== null ? (
            <Link
              to={targetHref}
              className={styles.rowLink}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
            >
              {task.title}
            </Link>
          ) : (
            <span className={styles.rowTarget} title={task.title}>
              {task.title}
            </span>
          ))}
        {!open && task.error !== null && (
          <span className={styles.rowError} title={task.error}>
            {task.error}
          </span>
        )}
      </div>
      <CountPills task={task} depth={depth} />
      <span className={`${styles.rowDuration} mo-mono`}>{duration}</span>
      <span className={`${styles.rowId} mo-mono`}>#{task.id}</span>
      <div
        className={styles.rowActions}
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
      </div>
    </div>
  );
}

function TaskNode({
  task,
  depth,
  opened,
  onToggle,
  actions,
  librariesByName,
}: {
  task: TaskRow;
  depth: number;
  opened: ReadonlySet<number>;
  onToggle: (id: number) => void;
  actions: TaskNodeActions;
  librariesByName: Map<string, Library>;
}) {
  const queryClient = useQueryClient();
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

  const cachedDetail = detail.data ?? queryClient.getQueryData<TaskDetail>(["task-detail", task.id]);
  const targetHref = resolveTargetHref(task, librariesByName, cachedDetail);
  const detailStyle = { "--depth": depth } as CSSProperties;

  return (
    <div>
      <NodeRow
        task={task}
        depth={depth}
        open={open}
        onToggle={() => onToggle(task.id)}
        actions={actions}
        onDelete={() => setConfirming(true)}
        targetHref={targetHref}
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
        <>
          <div className={styles.detailPanel} style={detailStyle}>
            {detail.isPending && <Loader size="xs" />}
            {detail.isError && (
              <Text size="sm" c="var(--mo-bad)">
                读不到任务详情：{detail.error.message}
              </Text>
            )}
            {detail.isSuccess && <TaskResultPanel detail={detail.data} />}
          </div>
          {task.child_count > 0 && (
            <div>
              {children.isPending && (
                <div className={styles.detailPanel} style={detailStyle}>
                  <Loader size="xs" />
                </div>
              )}
              {children.isError && (
                <div className={styles.detailPanel} style={detailStyle}>
                  <Text size="sm" c="var(--mo-bad)">
                    读不到子任务：{children.error.message}
                  </Text>
                </div>
              )}
              {children.data?.map((child) => (
                <TaskNode
                  key={child.id}
                  task={child}
                  depth={depth + 1}
                  opened={opened}
                  onToggle={onToggle}
                  actions={actions}
                  librariesByName={librariesByName}
                />
              ))}
            </div>
          )}
        </>
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
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const librariesByName = useMemo(() => {
    const map = new Map<string, Library>();
    const duplicates = new Set<string>();
    for (const lib of libraries.data ?? []) {
      if (map.has(lib.name)) {
        duplicates.add(lib.name);
      } else {
        map.set(lib.name, lib);
      }
    }
    for (const dup of duplicates) {
      map.delete(dup);
    }
    return map;
  }, [libraries.data]);

  return (
    <div className={styles.taskList}>
      {tasks.map((task) => (
        <TaskNode
          key={task.id}
          task={task}
          depth={0}
          opened={opened}
          onToggle={onToggle}
          actions={actions}
          librariesByName={librariesByName}
        />
      ))}
    </div>
  );
}
