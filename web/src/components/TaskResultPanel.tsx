import {
  Alert,
  Anchor,
  Collapse,
  Group,
  Progress,
  ScrollArea,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { fetchTaskLog, isTaskBusy, type TaskDetail } from "../api";
import { formatDuration, formatTime } from "../format";
import { IconChevronDown, IconChevronRight, IconExternal } from "../icons";
import styles from "../pages/TasksPage.module.css";
import { TaskStatusBadge } from "./TaskStatusBadge";
import { Pill } from "./ui";

/** 刮削结果里每个来源的成败。 */
type Outcome = "ok" | "cached" | "not_found" | "failed" | "skipped";

interface SourceOutcome {
  id: string;
  name: string;
  outcome: Outcome;
  detail: string | null;
}

const LOG_TIME_RE = /^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})\s+(.*)$/;
const LOG_ERROR_RE =
  /出错|失败|Traceback|Exception|Error:|Errno|WinError|未达到|已取消|^\s*(File "|at |\^+)/i;
const LOG_WARN_RE = /警告|warning|\bwarn\b|低于阈值|未识别|没有填|没有启用|跳过|找不到/i;

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

function formatSourceText(row: SourceOutcome): string {
  switch (row.outcome) {
    case "ok":
      return `✓ ${row.name}`;
    case "cached":
      return `● ${row.name} 缓存`;
    case "not_found":
      return `○ ${row.name} 没找到`;
    case "failed":
      return `✕ ${row.name} 失败`;
    case "skipped":
      return `${row.name} 跳过`;
  }
}

function classifyLogLine(body: string): "error" | "warn" | "normal" {
  if (LOG_ERROR_RE.test(body)) {
    return "error";
  }
  if (LOG_WARN_RE.test(body)) {
    return "warn";
  }
  return "normal";
}

function CollapsibleJson({ title, value }: { title: string; value: unknown }) {
  const [opened, setOpened] = useState(false);
  return (
    <div>
      <UnstyledButton className={styles.jsonToggle} onClick={() => setOpened((open) => !open)}>
        {opened ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
        <span>{title}</span>
      </UnstyledButton>
      <Collapse expanded={opened}>
        <ScrollArea.Autosize mah={220} type="auto" mt={6}>
          <pre className={styles.jsonPre}>{JSON.stringify(value, null, 2)}</pre>
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
    <Anchor component={Link} to={`/items/${detail.item.id}`} className={styles.itemLink}>
      <span>打开条目《{detail.item.title}》</span>
      <IconExternal size={12} />
    </Anchor>
  );
}

function OutcomeStrip({ sources }: { sources: SourceOutcome[] }) {
  if (sources.length === 0) {
    return null;
  }
  return (
    <div className={styles.sourceStrip}>
      {sources.map((row) => (
        <span
          key={`${row.outcome}-${row.id}-${row.name}`}
          className={styles.sourceChip}
          data-outcome={row.outcome}
          title={row.detail ?? undefined}
        >
          {formatSourceText(row)}
        </span>
      ))}
    </div>
  );
}

function ResultSummary({ detail }: { detail: TaskDetail }) {
  const result = detail.result;
  if (result === null) {
    return detail.item !== null ? <ItemLink detail={detail} /> : null;
  }
  const type = detail.task_type;
  if (type === "scrape") {
    const sources = readSources(result.sources);
    const images = count(result.images);
    return (
      <Stack gap={8}>
        <Group gap="md" wrap="wrap" align="center">
          <ItemLink detail={detail} />
          <Text size="xs" c="var(--mo-dim)">
            下载图片 {images} 张
          </Text>
        </Group>
        <OutcomeStrip sources={sources} />
      </Stack>
    );
  }
  if (type === "organize") {
    const unmatched = count(result.unmatched);
    return (
      <Group gap="xs" wrap="wrap" align="center">
        <Pill tone="ok">找到 {count(result.found)}</Pill>
        <Pill tone="accent">新增 {count(result.added)}</Pill>
        <Pill tone="neutral">移除 {count(result.removed)}</Pill>
        <Pill tone="ok">认出 {count(result.matched)}</Pill>
        <Pill tone={unmatched > 0 ? "warn" : "neutral"}>未识别 {unmatched}</Pill>
        <Text size="xs" c="var(--mo-dim)">
          排了 {count(result.scrape_queued)} 个刮削任务
        </Text>
      </Group>
    );
  }
  if (type === "arrange" || type === "write_files" || type === "rename") {
    const folder = text(result.folder);
    return (
      <Stack gap={6}>
        <Group gap="md" wrap="wrap" align="center">
          <ItemLink detail={detail} />
          <Text size="xs" c="var(--mo-dim)">
            写出 {count(result.written)} 个文件
          </Text>
        </Group>
        {folder !== null && (
          <Text size="xs" c="var(--mo-dim)" className="mo-mono" style={{ wordBreak: "break-all" }}>
            目录：{folder}
          </Text>
        )}
      </Stack>
    );
  }
  if (type === "scrape_library") {
    return (
      <Text size="xs" c="var(--mo-dim)">
        排了 {count(result.scrape_queued)} 个刮削任务
      </Text>
    );
  }
  if (type === "arrange_library") {
    return (
      <Text size="xs" c="var(--mo-dim)">
        排了 {count(result.queued)} 个整理任务
      </Text>
    );
  }
  return detail.item !== null ? <ItemLink detail={detail} /> : null;
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
    <div className={styles.logBox} aria-label="运行日志">
      {lines.length === 0 ? (
        <div className={styles.logEmpty}>（没有日志）</div>
      ) : (
        lines.map((raw, index) => {
          const match = LOG_TIME_RE.exec(raw);
          const stamp = match ? match[1] : null;
          const body = match ? match[2] : raw;
          const level = classifyLogLine(body);
          return (
            <div key={index} className={styles.logLine} data-level={level}>
              {stamp !== null && <span className={styles.logTime}>{stamp}</span>}
              <span>{body}</span>
            </div>
          );
        })
      )}
    </div>
  );
}

/** 展开一个任务后的信息、结果和日志。 */
export function TaskResultPanel({ detail }: { detail: TaskDetail }) {
  const duration = formatDuration(detail.started_at, detail.finished_at);
  return (
    <Stack gap="sm">
      <div className={styles.factsBar}>
        <TaskStatusBadge status={detail.status} variant="pill" />
        <span className={styles.factItem}>
          <span className={styles.factLabel}>编号</span>
          <span className="mo-mono">#{detail.id}</span>
        </span>
        {detail.root_task_id !== null && detail.root_task_id !== detail.id && (
          <span className={styles.factItem}>
            <span className={styles.factLabel}>根任务</span>
            <span className="mo-mono">#{detail.root_task_id}</span>
          </span>
        )}
        <span className={styles.factItem}>
          <span className={styles.factLabel}>优先级</span>
          <span className="mo-mono">{detail.priority}</span>
        </span>
        <span className={styles.factItem}>
          <span className={styles.factLabel}>创建</span>
          <span className="mo-mono">{formatTime(detail.created_at)}</span>
        </span>
        <span className={styles.factItem}>
          <span className={styles.factLabel}>开始</span>
          <span className="mo-mono">{formatTime(detail.started_at)}</span>
        </span>
        <span className={styles.factItem}>
          <span className={styles.factLabel}>耗时</span>
          <span className="mo-mono">{duration === "" ? "—" : duration}</span>
        </span>
      </div>

      {detail.status === "running" && (
        <Stack gap={4}>
          <Progress
            value={
              detail.progress === null
                ? 100
                : (detail.progress.current / Math.max(detail.progress.total, 1)) * 100
            }
            animated={detail.progress === null || detail.progress.total === 0}
            size={4}
            radius="xl"
          />
          {detail.progress !== null && (
            <Text size="xs" c="var(--mo-dim)">
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

      <ResultSummary detail={detail} />

      <TaskLog detail={detail} />

      <Group gap="md" wrap="wrap" align="flex-start">
        <CollapsibleJson title="参数" value={detail.payload} />
        {detail.result !== null && <CollapsibleJson title="原始结果" value={detail.result} />}
      </Group>
    </Stack>
  );
}
