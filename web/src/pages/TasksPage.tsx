import {
  Alert,
  Button,
  Group,
  Loader,
  Menu,
  Modal,
  Pagination,
  Stack,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  type BulkAction,
  TASK_PAGE_SIZE,
  type TaskAction,
  type TaskFilter,
  type TaskRow,
  batchTasks,
  bulkTasks,
  fetchTasks,
  fetchWorker,
  isTaskBusy,
  setWorkerPaused,
} from "../api";
import { TaskTree } from "../components/TaskTree";
import { NowRunningCard } from "../components/tasks/NowRunningCard";
import { FilterChip, PageHeader, Pill } from "../components/ui";
import {
  IconClose,
  IconMore,
  IconPause,
  IconPlay,
  IconRefresh,
  IconTrash,
} from "../icons";
import styles from "./TasksPage.module.css";

/** 批量操作在确认框里的文案和完成后的提示。 */
const BULK_LABELS: Record<
  BulkAction,
  { button: string; title: string; message: string; done: (n: number) => string; danger?: boolean }
> = {
  cancel_active: {
    button: "取消进行中",
    title: "取消进行中的任务",
    message: "会取消所有排队中和运行中的任务，正在跑的中途停下。继续？",
    done: (n) => `取消了 ${n} 个任务`,
  },
  retry_failed: {
    button: "重试失败",
    title: "重试失败的任务",
    message: "会给每个失败的任务按原样重新排一个。继续？",
    done: (n) => `重新排了 ${n} 个任务`,
  },
  clear_succeeded: {
    button: "清除已完成",
    title: "清除已完成的任务",
    message: "会删除已完成的根任务记录和日志，硬盘上的文件不动。继续？",
    done: (n) => `清除了 ${n} 个已完成的任务`,
    danger: true,
  },
  clear_failed: {
    button: "清除失败",
    title: "清除失败的任务",
    message: "会删除失败的任务记录和日志，硬盘上的文件不动。继续？",
    done: (n) => `清除了 ${n} 个失败的任务`,
    danger: true,
  },
};

const ACTION_LABELS: Record<TaskAction, string> = { cancel: "取消", retry: "重试", delete: "删除" };
const DATE_GROUPS = ["今天", "昨天", "更早"] as const;
type DateGroup = (typeof DATE_GROUPS)[number];

function dateGroupOf(iso: string): DateGroup {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "更早";
  }
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const itemStart = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const diffDays = Math.round((todayStart - itemStart) / 86_400_000);
  if (diffDays <= 0) {
    return "今天";
  }
  if (diffDays === 1) {
    return "昨天";
  }
  return "更早";
}

function groupByDate(items: readonly TaskRow[]): { label: DateGroup; items: TaskRow[] }[] {
  const buckets = new Map<DateGroup, TaskRow[]>(DATE_GROUPS.map((label) => [label, []]));
  for (const task of items) {
    buckets.get(dateGroupOf(task.created_at))?.push(task);
  }
  return DATE_GROUPS.map((label) => ({ label, items: buckets.get(label) ?? [] })).filter(
    (group) => group.items.length > 0,
  );
}

function bulkIcon(action: BulkAction) {
  switch (action) {
    case "cancel_active":
      return <IconClose size={13} />;
    case "retry_failed":
      return <IconRefresh size={13} />;
    case "clear_succeeded":
    case "clear_failed":
      return <IconTrash size={13} />;
  }
}

function BulkConfirm({
  action,
  pending,
  onClose,
  onConfirm,
}: {
  action: BulkAction;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const labels = BULK_LABELS[action];
  return (
    <Modal opened onClose={onClose} title={labels.title}>
      <Stack>
        <Text size="sm">{labels.message}</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            color={labels.danger === true ? "red" : undefined}
            loading={pending}
            onClick={onConfirm}
          >
            {labels.button}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function TasksPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [page, setPage] = useState(1);
  const [opened, setOpened] = useState<ReadonlySet<number>>(new Set());
  const [confirming, setConfirming] = useState<BulkAction | null>(null);

  const tasks = useQuery({
    queryKey: ["tasks", filter, page],
    queryFn: () => fetchTasks(filter, (page - 1) * TASK_PAGE_SIZE),
    refetchInterval: (query) => (query.state.data?.items.some(isTaskBusy) ? 2000 : 10000),
  });
  // 与侧栏共享 ["tasks", "active", 0] 缓存，拿到进行中总数和当前正在跑的根任务。
  const activeQuery = useQuery({
    queryKey: ["tasks", "active", 0],
    queryFn: () => fetchTasks("active"),
    refetchInterval: 3000,
  });
  const allSummary = useQuery({
    queryKey: ["tasks", "all", 1],
    queryFn: () => fetchTasks("all", 0),
    enabled: filter !== "all" || page !== 1,
    refetchInterval: (query) => (query.state.data?.items.some(isTaskBusy) ? 2000 : 10000),
  });
  const failedSummary = useQuery({
    queryKey: ["tasks", "failed", 1],
    queryFn: () => fetchTasks("failed", 0),
    enabled: filter !== "failed" || page !== 1,
    refetchInterval: 10000,
  });
  const worker = useQuery({
    queryKey: ["task-worker"],
    queryFn: fetchWorker,
    refetchInterval: 10000,
  });
  const paused = worker.data?.paused === true;

  const invalidate = () => {
    for (const key of [["tasks"], ["task-children"], ["task-detail"], ["task-log"], ["task"]]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };

  // 任务从忙碌转为全部空闲时刷新一次关联列表、详情与媒体库计数。
  const hasBusy =
    (tasks.data?.items.some(isTaskBusy) ?? false) ||
    (activeQuery.data?.items.some(isTaskBusy) ?? false);
  const wasBusyRef = useRef(false);
  useEffect(() => {
    if (wasBusyRef.current && !hasBusy) {
      for (const key of [
        ["tasks"],
        ["task-children"],
        ["task-detail"],
        ["task-log"],
        ["libraries"],
        ["unmatched"],
      ]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    }
    wasBusyRef.current = hasBusy;
  }, [hasBusy, queryClient]);

  const act = useMutation({
    mutationFn: ({ action, id }: { action: TaskAction; id: number }) => batchTasks(action, [id]),
    onSuccess: (_result, { action }) => {
      invalidate();
      notifications.show({ message: `已${ACTION_LABELS[action]}` });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });
  const bulk = useMutation({
    mutationFn: (action: BulkAction) => bulkTasks(action),
    onSuccess: ({ affected }, action) => {
      setConfirming(null);
      invalidate();
      notifications.show({ message: BULK_LABELS[action].done(affected) });
    },
    onError: (error) => {
      setConfirming(null);
      notifications.show({ color: "red", message: error.message });
    },
  });
  const pause = useMutation({
    mutationFn: (nextPaused: boolean) => setWorkerPaused(nextPaused),
    onSuccess: (state) => {
      queryClient.setQueryData(["task-worker"], state);
      notifications.show({ message: state.paused ? "任务队列已暂停" : "任务队列已继续" });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  const items = tasks.data?.items ?? [];
  const grouped = useMemo(() => groupByDate(items), [items]);
  // 全部展开 / 收起只作用于当前页的根任务，子任务自己点。
  const expandable = items.filter((task) => task.child_count > 0);
  const canExpand = expandable.some((task) => !opened.has(task.id));

  const allTotal =
    filter === "all" && page === 1 ? (tasks.data?.total ?? 0) : (allSummary.data?.total ?? 0);
  const activeTotal = activeQuery.data?.total ?? 0;
  const failedTotal =
    filter === "failed" && page === 1 ? (tasks.data?.total ?? 0) : (failedSummary.data?.total ?? 0);

  const activeItems = activeQuery.data?.items ?? [];
  const runningRoot =
    activeItems.find((task) => task.status === "running") ??
    activeItems.find((task) => (task.tree_status.running ?? 0) > 0) ??
    null;

  const toggle = (id: number) => {
    setOpened((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const switchFilter = (next: TaskFilter) => {
    setFilter(next);
    setPage(1);
    setOpened(new Set());
  };

  return (
    <Stack gap={0}>
      <PageHeader
        title="任务"
        pills={
          <>
            <Pill>共 {allTotal} 条</Pill>
            <Pill tone="accent">{activeTotal} 进行中</Pill>
            {failedTotal > 0 && <Pill tone="bad">{failedTotal} 失败</Pill>}
          </>
        }
        description="支持展开任务树查看子步骤、来源命中结果与运行日志"
        actions={
          <>
            <Button
              variant={paused ? "filled" : "default"}
              leftSection={paused ? <IconPlay size={14} /> : <IconPause size={14} />}
              loading={pause.isPending}
              onClick={() => pause.mutate(!paused)}
            >
              {paused ? "继续队列" : "暂停队列"}
            </Button>
            <Menu position="bottom-end" shadow="md">
              <Menu.Target>
                <Button variant="default" px={10} aria-label="更多操作">
                  <IconMore size={15} />
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconRefresh size={14} />} onClick={invalidate}>
                  刷新任务列表
                </Menu.Item>
                <Menu.Divider />
                {(Object.keys(BULK_LABELS) as BulkAction[]).map((action) => (
                  <Menu.Item
                    key={action}
                    color={BULK_LABELS[action].danger === true ? "red" : undefined}
                    leftSection={bulkIcon(action)}
                    disabled={bulk.isPending}
                    onClick={() => setConfirming(action)}
                  >
                    {BULK_LABELS[action].button}
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
          </>
        }
      />

      {paused && (
        <Alert color="yellow" title="队列已暂停" mb="md">
          任务队列已暂停，正在跑的会跑完，新任务排队等待。
        </Alert>
      )}

      {runningRoot !== null && (
        <NowRunningCard
          task={runningRoot}
          cancelling={act.isPending}
          onCancel={(id) => act.mutate({ action: "cancel", id })}
        />
      )}

      <div className={styles.toolbar}>
        <div className={styles.chips}>
          <FilterChip
            active={filter === "all"}
            count={allTotal}
            onClick={() => switchFilter("all")}
          >
            全部
          </FilterChip>
          <FilterChip
            active={filter === "active"}
            count={activeTotal}
            onClick={() => switchFilter("active")}
          >
            进行中
          </FilterChip>
          <FilterChip
            active={filter === "failed"}
            count={failedTotal}
            onClick={() => switchFilter("failed")}
          >
            失败
          </FilterChip>
        </div>

        <div className={styles.toolbarActions}>
          <Button
            size="xs"
            variant="subtle"
            disabled={!canExpand}
            onClick={() => setOpened(new Set(expandable.map((task) => task.id)))}
          >
            全部展开
          </Button>
          <Button
            size="xs"
            variant="subtle"
            disabled={opened.size === 0}
            onClick={() => setOpened(new Set())}
          >
            全部收起
          </Button>
          {(Object.keys(BULK_LABELS) as BulkAction[]).map((action) => (
            <Button
              key={action}
              size="xs"
              variant="default"
              leftSection={bulkIcon(action)}
              disabled={bulk.isPending}
              onClick={() => setConfirming(action)}
            >
              {BULK_LABELS[action].button}
            </Button>
          ))}
        </div>
      </div>

      {tasks.isPending && <Loader size="sm" mt="sm" />}
      {tasks.isError && (
        <Alert color="red" title="读不到任务" mt="sm">
          {tasks.error.message}
        </Alert>
      )}
      {tasks.isSuccess && tasks.data.total === 0 && (
        <Text c="var(--mo-dim)" size="sm" mt="sm">
          没有任务。
        </Text>
      )}

      {tasks.isSuccess &&
        tasks.data.total > 0 &&
        grouped.map((group) => (
          <section key={group.label}>
            <div className={styles.dayLabel}>{group.label}</div>
            <TaskTree
              tasks={group.items}
              opened={opened}
              onToggle={toggle}
              actions={{
                onCancel: (id) => act.mutate({ action: "cancel", id }),
                onRetry: (id) => act.mutate({ action: "retry", id }),
                onDelete: (id) => act.mutate({ action: "delete", id }),
                pending: act.isPending,
              }}
            />
          </section>
        ))}

      {tasks.isSuccess && tasks.data.total > TASK_PAGE_SIZE && (
        <Group mt="lg">
          <Pagination
            total={Math.ceil(tasks.data.total / TASK_PAGE_SIZE)}
            value={page}
            onChange={setPage}
          />
        </Group>
      )}

      {confirming !== null && (
        <BulkConfirm
          action={confirming}
          pending={bulk.isPending}
          onClose={() => setConfirming(null)}
          onConfirm={() => bulk.mutate(confirming)}
        />
      )}
    </Stack>
  );
}
