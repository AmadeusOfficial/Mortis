import {
  Alert,
  Anchor,
  Badge,
  Code,
  Collapse,
  Group,
  Progress,
  ScrollArea,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";
import { fetchTaskLog, isTaskBusy, type TaskDetail } from "../api";
import { formatDuration, formatTime } from "../format";
import { TaskStatusBadge } from "./TaskStatusBadge";

/** 刮削结果里每个来源的成败。 */
type Outcome = "ok" | "cached" | "not_found" | "failed" | "skipped";

interface SourceOutcome {
  id: string;
  name: string;
  outcome: Outcome;
  detail: string | null;
}

const OUTCOME_GROUPS: { outcome: Outcome; label: string; color: string }[] = [
  { outcome: "ok", label: "成功", color: "green" },
  { outcome: "cached", label: "用了快照", color: "gray" },
  { outcome: "not_found", label: "没找到", color: "yellow" },
  { outcome: "failed", label: "出错", color: "red" },
  { outcome: "skipped", label: "未启用", color: "gray" },
];

function count(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function readSources(value: unknown): SourceOutcome[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const rows: SourceOutcome[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null) {
      continue;
    }
    const record = entry as Record<string, unknown>;
    const outcome = record.outcome;
    if (
      outcome !== "ok" &&
      outcome !== "cached" &&
      outcome !== "not_found" &&
      outcome !== "failed" &&
      outcome !== "skipped"
    ) {
      continue;
    }
    rows.push({
      id: text(record.id) ?? "",
      name: text(record.name) ?? text(record.id) ?? "来源",
      outcome,
      detail: text(record.detail),
    });
  }
  return rows;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" mt={2}>
        {children}
      </Text>
    </div>
  );
}

function CollapsibleJson({ title, value }: { title: string; value: unknown }) {
  const [opened, setOpened] = useState(false);
  return (
    <div>
      <UnstyledButton onClick={() => setOpened((open) => !open)}>
        <Text size="sm" c="dimmed" fw={500}>
          {opened ? "▾" : "▸"} {title}
        </Text>
      </UnstyledButton>
      <Collapse expanded={opened}>
        <ScrollArea.Autosize mah={220} type="auto" mt={6}>
          <Code block>{JSON.stringify(value, null, 2)}</Code>
        </ScrollArea.Autosize>
      </Collapse>
    </div>
  );
}

function ItemLink({ detail }: { detail: TaskDetail }) {
  if (detail.item === null) {
    return null;
  }
  return (
    <Group gap={6} wrap="nowrap" align="baseline">
      <Text size="xs" c="dimmed">
        条目
      </Text>
      <Anchor component={Link} to={`/items/${detail.item.id}`} size="sm" fw={500}>
        {`查看《${detail.item.title}》`}
      </Anchor>
    </Group>
  );
}

function OutcomeSection({ sources }: { sources: SourceOutcome[] }) {
  const groups = OUTCOME_GROUPS.map((group) => ({
    ...group,
    rows: sources.filter((row) => row.outcome === group.outcome),
  })).filter((group) => group.rows.length > 0);
  if (groups.length === 0) {
    return null;
  }
  return (
    <Stack gap="xs">
      {groups.map((group) => (
        <Stack key={group.outcome} gap={4}>
          <Group gap={6} wrap="nowrap">
            <Text size="sm" fw={600}>
              {group.label}
            </Text>
            <Text size="xs" c="dimmed">
              {group.rows.length}
            </Text>
          </Group>
          {group.outcome === "ok" || group.outcome === "cached" ? (
            <Group gap={6}>
              {group.rows.map((row) => (
                <Badge
                  key={`${row.outcome}-${row.id}-${row.name}`}
                  size="sm"
                  variant="light"
                  color={group.color}
                  tt="none"
                >
                  {row.name}
                </Badge>
              ))}
            </Group>
          ) : (
            <Stack gap={2}>
              {group.rows.map((row) => (
                <Text
                  key={`${row.outcome}-${row.id}-${row.name}`}
                  size="sm"
                  c={group.outcome === "failed" ? "red" : undefined}
                  style={{ wordBreak: "break-word" }}
                >
                  {row.detail === null ? row.name : `${row.name}：${row.detail}`}
                </Text>
              ))}
            </Stack>
          )}
        </Stack>
      ))}
    </Stack>
  );
}

function ResultRows({ detail }: { detail: TaskDetail }) {
  const result = detail.result;
  if (result === null) {
    return null;
  }
  const type = detail.task_type;
  if (type === "scrape") {
    return (
      <Stack gap="xs">
        <ItemLink detail={detail} />
        <OutcomeSection sources={readSources(result.sources)} />
        <Text size="sm">下载图片 {count(result.images)} 张</Text>
      </Stack>
    );
  }
  if (type === "organize") {
    return (
      <Group gap="xs">
        <Badge variant="light" color="green">
          找到 {count(result.found)}
        </Badge>
        <Badge variant="light" color="blue">
          新增 {count(result.added)}
        </Badge>
        <Badge variant="light" color="gray">
          移除 {count(result.removed)}
        </Badge>
        <Badge variant="light" color="teal">
          认出 {count(result.matched)}
        </Badge>
        <Badge variant="light" color={count(result.unmatched) > 0 ? "yellow" : "gray"}>
          未识别 {count(result.unmatched)}
        </Badge>
        <Text size="sm" c="dimmed">
          排了 {count(result.scrape_queued)} 个刮削任务
        </Text>
      </Group>
    );
  }
  if (type === "arrange" || type === "write_files" || type === "rename") {
    const folder = text(result.folder);
    return (
      <Stack gap="xs">
        <ItemLink detail={detail} />
        {folder !== null && (
          <Group gap={6} wrap="nowrap" align="baseline">
            <Text size="xs" c="dimmed">
              目录
            </Text>
            <Text size="sm" ff="monospace" style={{ wordBreak: "break-all" }}>
              {folder}
            </Text>
          </Group>
        )}
        <Text size="sm">写出 {count(result.written)} 个文件</Text>
      </Stack>
    );
  }
  if (type === "scrape_library") {
    return <Text size="sm">排了 {count(result.scrape_queued)} 个刮削任务</Text>;
  }
  if (type === "arrange_library") {
    return <Text size="sm">排了 {count(result.queued)} 个整理任务</Text>;
  }
  return null;
}

function TaskLog({ detail }: { detail: TaskDetail }) {
  const busy = isTaskBusy(detail);
  const log = useQuery({
    queryKey: ["task-log", detail.id],
    queryFn: () => fetchTaskLog(detail.id),
    refetchInterval: busy ? 2000 : false,
  });
  const lines = log.data?.lines ?? [];
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        日志
      </Text>
      <ScrollArea.Autosize mah={240} type="auto">
        <Code block>{lines.length > 0 ? lines.join("\n") : "（没有日志）"}</Code>
      </ScrollArea.Autosize>
    </Stack>
  );
}

/** 展开一个任务后的信息、结果和日志。 */
export function TaskResultPanel({ detail }: { detail: TaskDetail }) {
  const duration = formatDuration(detail.started_at, detail.finished_at);
  return (
    <Stack gap="sm">
      <Group gap="lg" wrap="wrap" align="flex-start">
        <Fact label="编号">#{detail.id}</Fact>
        {detail.root_task_id !== null && detail.root_task_id !== detail.id && (
          <Fact label="根任务">#{detail.root_task_id}</Fact>
        )}
        <Fact label="优先级">{detail.priority}</Fact>
        <Fact label="创建">{formatTime(detail.created_at)}</Fact>
        <Fact label="开始">{formatTime(detail.started_at)}</Fact>
        <Fact label="耗时">{duration === "" ? "—" : duration}</Fact>
        <div>
          <Text size="xs" c="dimmed">
            状态
          </Text>
          <div style={{ marginTop: 2 }}>
            <TaskStatusBadge status={detail.status} />
          </div>
        </div>
      </Group>
      {detail.status === "running" && (
        <Stack gap={4}>
          <Progress
            value={
              detail.progress === null
                ? 100
                : (detail.progress.current / Math.max(detail.progress.total, 1)) * 100
            }
            animated={detail.progress === null || detail.progress.total === 0}
          />
          {detail.progress !== null && (
            <Text size="xs" c="dimmed">
              {detail.progress.current}/{detail.progress.total} {detail.progress.message}
            </Text>
          )}
        </Stack>
      )}
      {detail.status === "failed" && detail.error !== null && (
        <Alert color="red" title="任务失败">
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {detail.error}
          </Text>
        </Alert>
      )}
      <ResultRows detail={detail} />
      <CollapsibleJson title="参数" value={detail.payload} />
      {detail.result !== null && <CollapsibleJson title="结果" value={detail.result} />}
      <TaskLog detail={detail} />
    </Stack>
  );
}
