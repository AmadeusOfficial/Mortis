import {
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  Pagination,
  SegmentedControl,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type BulkAction,
  TASK_PAGE_SIZE,
  type TaskAction,
  type TaskFilter,
  batchTasks,
  bulkTasks,
  fetchTasks,
  fetchWorker,
  isTaskBusy,
  setWorkerPaused,
} from "../api";
import { TaskTree } from "../components/TaskTree";

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
    mutationFn: (paused: boolean) => setWorkerPaused(paused),
    onSuccess: (state) => {
      queryClient.setQueryData(["task-worker"], state);
      notifications.show({ message: state.paused ? "任务队列已暂停" : "任务队列已继续" });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  const items = tasks.data?.items ?? [];
  // 全部展开 / 收起只作用于当前页的根任务，子任务自己点。
  const expandable = items.filter((task) => task.child_count > 0);
  const canExpand = expandable.some((task) => !opened.has(task.id));

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

  return (
    <Stack>
      <Group justify="space-between" wrap="wrap">
        <Group gap="sm" align="baseline">
          <Title order={2}>任务</Title>
          <Text size="sm" c="dimmed">
            共 {tasks.data?.total ?? 0} 条
          </Text>
        </Group>
        <Group gap="xs" wrap="wrap">
          <Button
            variant={paused ? "filled" : "default"}
            loading={pause.isPending}
            onClick={() => pause.mutate(!paused)}
          >
            {paused ? "继续" : "暂停"}
          </Button>
          <SegmentedControl
            value={filter}
            onChange={(value) => {
              setFilter(value as TaskFilter);
              setPage(1);
              setOpened(new Set());
            }}
            data={[
              { value: "all", label: "全部" },
              { value: "active", label: "进行中" },
              { value: "failed", label: "失败" },
            ]}
          />
        </Group>
      </Group>

      {paused && (
        <Alert color="yellow" title="队列已暂停">
          任务队列已暂停，正在跑的会跑完，新任务排队等待。
        </Alert>
      )}

      <Group gap="xs" wrap="wrap">
        <Button
          size="xs"
          variant="light"
          disabled={!canExpand}
          onClick={() => setOpened(new Set(expandable.map((task) => task.id)))}
        >
          全部展开
        </Button>
        <Button
          size="xs"
          variant="light"
          disabled={opened.size === 0}
          onClick={() => setOpened(new Set())}
        >
          全部收起
        </Button>
        <Group gap="xs" wrap="wrap">
          {(Object.keys(BULK_LABELS) as BulkAction[]).map((action) => (
            <Button
              key={action}
              size="xs"
              variant="light"
              color={BULK_LABELS[action].danger === true ? "red" : undefined}
              disabled={bulk.isPending}
              onClick={() => setConfirming(action)}
            >
              {BULK_LABELS[action].button}
            </Button>
          ))}
        </Group>
      </Group>

      {tasks.isPending && <Loader size="sm" />}
      {tasks.isError && (
        <Alert color="red" title="读不到任务">
          {tasks.error.message}
        </Alert>
      )}
      {tasks.isSuccess && tasks.data.total === 0 && <Text c="dimmed">没有任务。</Text>}
      {tasks.isSuccess && tasks.data.total > 0 && (
        <TaskTree
          tasks={items}
          opened={opened}
          onToggle={toggle}
          actions={{
            onCancel: (id) => act.mutate({ action: "cancel", id }),
            onRetry: (id) => act.mutate({ action: "retry", id }),
            onDelete: (id) => act.mutate({ action: "delete", id }),
            pending: act.isPending,
          }}
        />
      )}
      {tasks.isSuccess && tasks.data.total > TASK_PAGE_SIZE && (
        <Pagination
          total={Math.ceil(tasks.data.total / TASK_PAGE_SIZE)}
          value={page}
          onChange={setPage}
        />
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
