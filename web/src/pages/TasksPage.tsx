import {
  Alert,
  Button,
  Code,
  Group,
  Loader,
  Pagination,
  Paper,
  Progress,
  ScrollArea,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import {
  TASK_PAGE_SIZE,
  type TaskAction,
  type TaskFilter,
  type TaskRow,
  batchTasks,
  clearFinishedTasks,
  fetchTaskChildren,
  fetchTaskLog,
  fetchTasks,
  isTaskBusy,
} from "../api";
import { TaskStatusBadge } from "../components/TaskStatusBadge";
import { formatTime } from "../format";

const ACTION_LABELS: Record<TaskAction, string> = { cancel: "取消", retry: "重试", delete: "删除" };

function childSummary(task: TaskRow): string {
  if (task.child_count === 0) {
    return "";
  }
  const done =
    (task.child_status.succeeded ?? 0) +
    (task.child_status.failed ?? 0) +
    (task.child_status.cancelled ?? 0);
  const failed = task.child_status.failed ?? 0;
  return `子任务 ${done}/${task.child_count}${failed > 0 ? `，失败 ${failed}` : ""}`;
}

function actionsFor(task: TaskRow): TaskAction[] {
  if (isTaskBusy(task)) {
    return ["cancel"];
  }
  return task.status === "failed" || task.status === "cancelled" ? ["retry", "delete"] : ["delete"];
}

export function TasksPage() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<number | null>(null);
  const tasks = useQuery({
    queryKey: ["tasks", filter, page],
    queryFn: () => fetchTasks(filter, (page - 1) * TASK_PAGE_SIZE),
    refetchInterval: (query) => (query.state.data?.items.some(isTaskBusy) ? 2000 : 10000),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["tasks"] });
  const act = useMutation({
    mutationFn: ({ action, id }: { action: TaskAction; id: number }) => batchTasks(action, [id]),
    onSuccess: (_result, { action }) => {
      refresh();
      notifications.show({ message: `已${ACTION_LABELS[action]}` });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });
  const clear = useMutation({
    mutationFn: clearFinishedTasks,
    onSuccess: ({ affected }) => {
      refresh();
      notifications.show({ message: `清除了 ${affected} 个已结束的任务` });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>任务</Title>
        <Group>
          <SegmentedControl
            value={filter}
            onChange={(value) => {
              setFilter(value as TaskFilter);
              setPage(1);
            }}
            data={[
              { value: "all", label: "全部" },
              { value: "active", label: "进行中" },
              { value: "failed", label: "失败" },
            ]}
          />
          <Button variant="default" loading={clear.isPending} onClick={() => clear.mutate()}>
            清除已结束
          </Button>
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
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>任务</Table.Th>
              <Table.Th>状态</Table.Th>
              <Table.Th>进度</Table.Th>
              <Table.Th>开始</Table.Th>
              <Table.Th>结束</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tasks.data.items.map((task) => (
              <Fragment key={task.id}>
                <Table.Tr
                  style={{ cursor: "pointer" }}
                  onClick={() => setExpanded(expanded === task.id ? null : task.id)}
                >
                  <Table.Td>
                    <Text size="sm" fw={500}>
                      {task.type_label}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {task.title}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <TaskStatusBadge status={task.status} />
                  </Table.Td>
                  <Table.Td>
                    {task.progress !== null && (
                      <Stack gap={2}>
                        <Progress
                          value={(task.progress.current / Math.max(task.progress.total, 1)) * 100}
                        />
                        <Text size="xs" c="dimmed">
                          {task.progress.current}/{task.progress.total} {task.progress.message}
                        </Text>
                      </Stack>
                    )}
                    <Text size="xs">{childSummary(task)}</Text>
                    {task.error !== null && (
                      <Text size="xs" c="red">
                        {task.error}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs">{formatTime(task.started_at)}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs">{formatTime(task.finished_at)}</Text>
                  </Table.Td>
                  <Table.Td onClick={(event) => event.stopPropagation()}>
                    <Group gap={4} wrap="nowrap">
                      {actionsFor(task).map((action) => (
                        <Button
                          key={action}
                          size="compact-xs"
                          variant="subtle"
                          color={action === "delete" ? "red" : undefined}
                          onClick={() => act.mutate({ action, id: task.id })}
                        >
                          {ACTION_LABELS[action]}
                        </Button>
                      ))}
                    </Group>
                  </Table.Td>
                </Table.Tr>
                {expanded === task.id && (
                  <Table.Tr>
                    <Table.Td colSpan={6}>
                      <TaskPanel task={task} />
                    </Table.Td>
                  </Table.Tr>
                )}
              </Fragment>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {tasks.isSuccess && tasks.data.total > TASK_PAGE_SIZE && (
        <Pagination
          total={Math.ceil(tasks.data.total / TASK_PAGE_SIZE)}
          value={page}
          onChange={setPage}
        />
      )}
    </Stack>
  );
}

function TaskPanel({ task }: { task: TaskRow }) {
  const busy = isTaskBusy(task);
  const children = useQuery({
    queryKey: ["task-children", task.id],
    queryFn: () => fetchTaskChildren(task.id),
    enabled: task.child_count > 0,
    refetchInterval: busy ? 2000 : false,
  });
  const log = useQuery({
    queryKey: ["task-log", task.id],
    queryFn: () => fetchTaskLog(task.id),
    refetchInterval: busy ? 2000 : false,
  });
  const lines = log.data?.lines ?? [];
  return (
    <Stack gap="sm">
      {task.child_count > 0 && (
        <Paper withBorder p="xs">
          <Text size="sm" fw={500} mb={4}>
            子任务
          </Text>
          {children.data?.map((child) => (
            <Group key={child.id} gap="xs" wrap="nowrap">
              <TaskStatusBadge status={child.status} />
              <Text size="sm">
                {child.type_label}：{child.title}
              </Text>
              {child.error !== null && (
                <Text size="xs" c="red">
                  {child.error}
                </Text>
              )}
            </Group>
          ))}
        </Paper>
      )}
      <Text size="sm" fw={500}>
        日志
      </Text>
      <ScrollArea h={240}>
        <Code block>{lines.length > 0 ? lines.join("\n") : "（没有日志）"}</Code>
      </ScrollArea>
    </Stack>
  );
}
