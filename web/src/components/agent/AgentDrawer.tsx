import { Alert, Anchor, Button, Drawer, Group, Loader, Stack, Text, Textarea, UnstyledButton } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import { Link, useLocation } from "react-router";
import { fetchLibraries, fetchSecretsStatus } from "../../api";
import { IconChevronDown, IconChevronRight, IconSparkle } from "../../icons";
import { Pill } from "../ui";
import { type Entry, NO_KEY, useAgent } from "./AgentContext";
import styles from "./AgentDrawer.module.css";
import type { ApprovalItem, SearchRow } from "./chatStream";

const TOOL_LABELS: Record<string, string> = {
  search_items: "检索",
  get_item: "查看",
  update_fields: "修改",
  scrape_item: "刮削",
  scan_library: "扫描",
  play_item: "播放",
};

const markdown: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function toolTarget(name: string, args: Record<string, unknown>, result: Record<string, unknown>): string {
  if (name === "search_items") return String(args.query ?? "");
  if (name === "scan_library") {
    return typeof result.library_name === "string" ? result.library_name : `库 ${String(args.library_id ?? "")}`;
  }
  if (typeof result.title === "string") return result.title;
  return args.item_id !== undefined ? `条目 ${String(args.item_id)}` : "";
}

function searchRows(result: Record<string, unknown>): SearchRow[] {
  return Array.isArray(result.items) ? (result.items as SearchRow[]) : [];
}

function ToolEntry({ entry }: { entry: Extract<Entry, { kind: "tool" }> }) {
  const [expanded, setExpanded] = useState(false);
  const args = record(entry.arguments);
  const result = record(entry.result);
  const pending = entry.result === undefined;
  const error = typeof result.error === "string" ? result.error : null;
  const label = TOOL_LABELS[entry.name] ?? entry.name;
  const target = toolTarget(entry.name, args, result);
  const rows = entry.name === "search_items" ? searchRows(result) : [];
  return (
    <div className={styles.tool}>
      <UnstyledButton
        className={styles.toolLine}
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        {expanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        <Text span size="xs" fw={600} truncate>
          {target ? `${label}：${target}` : label}
        </Text>
        {pending && <Loader size={12} />}
        {entry.name === "search_items" && !pending && error === null && (
          <Text span size="xs" c="var(--mo-muted)">
            {rows.length} 条
          </Text>
        )}
        {result.needs_choice === true && <Pill tone="warn">要选一个</Pill>}
        {result.played === true && <Pill tone="ok">已打开</Pill>}
        {error !== null && <Pill tone="bad">出错</Pill>}
      </UnstyledButton>
      {expanded && (
        <pre className={styles.toolDetail}>
          {JSON.stringify({ 参数: entry.arguments, 结果: entry.result ?? "还在执行" }, null, 2)}
        </pre>
      )}
      {error !== null && (
        <Text size="xs" c="var(--mo-bad)" px={10} pb={8}>
          {error}
        </Text>
      )}
      {rows.length > 0 && (
        <div className={styles.cards}>
          {rows.map((row) => (
            <Link key={row.id} to={`/items/${row.id}`} className={styles.card}>
              <Text size="sm" fw={600} truncate>
                {row.title}
              </Text>
              <Text size="xs" c="var(--mo-dim)" truncate>
                {[row.year, row.library_name, row.structure].filter((part) => part !== null && part !== "").join(" · ")}
              </Text>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join("、");
  if (value === null || value === undefined || value === "") return "（清空）";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function ApprovalLine({ item }: { item: ApprovalItem }) {
  if (item.tool === "update_fields") {
    return (
      <div>
        <Text size="sm" fw={600}>
          {item.title}
        </Text>
        {Object.entries(item.values ?? {}).map(([key, value]) => (
          <Text key={key} size="xs" c="var(--mo-dim)">
            <span className="mo-mono">{key}</span> → {formatValue(value)}
          </Text>
        ))}
      </div>
    );
  }
  return (
    <Text size="xs" className="mo-mono">
      {TOOL_LABELS[item.tool] ?? item.tool} {JSON.stringify(item.arguments ?? {})}
    </Text>
  );
}

function ApprovalCard({ entry }: { entry: Extract<Entry, { kind: "approval" }> }) {
  const { decide, busy } = useAgent();
  return (
    <div className={styles.approval} role="group" aria-label="需要批准的修改">
      <Text size="sm" fw={700} mb={6}>
        {entry.summary}
      </Text>
      <Stack gap={6} mb={10}>
        {entry.items.map((item, index) => (
          <ApprovalLine key={index} item={item} />
        ))}
      </Stack>
      {entry.status === "pending" ? (
        <Group gap="xs">
          <Button size="xs" disabled={busy} onClick={() => decide(entry.approvalId, true)}>
            批准
          </Button>
          <Button size="xs" variant="default" disabled={busy} onClick={() => decide(entry.approvalId, false)}>
            拒绝
          </Button>
        </Group>
      ) : (
        <Pill tone={entry.status === "approved" ? "ok" : "neutral"}>
          {entry.status === "approved" ? "已批准" : "已拒绝"}
        </Pill>
      )}
    </div>
  );
}

function EntryView({ entry }: { entry: Entry }) {
  switch (entry.kind) {
    case "user":
      return <div className={styles.user}>{entry.text}</div>;
    case "assistant":
      return (
        <div className={styles.answer}>
          <Markdown skipHtml components={markdown}>
            {entry.text}
          </Markdown>
        </div>
      );
    case "tool":
      return <ToolEntry entry={entry} />;
    case "approval":
      return <ApprovalCard entry={entry} />;
    case "error":
      return (
        <Alert color="red" variant="light" p="xs">
          {entry.message}
        </Alert>
      );
  }
}

/** 抽屉为空时的预设问题，点了填进输入框，不发送。 */
function usePresets(): string[] {
  const { pathname } = useLocation();
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const match = /^\/libraries\/(\d+)/.exec(pathname);
  const library = match ? libraries.data?.find((row) => row.id === Number(match[1])) : undefined;
  return [library ? `「${library.name}」里有什么？` : "库里都有些什么？", "播放"];
}

export function AgentDrawer() {
  const agent = useAgent();
  const { opened, close, entries, busy, draft, setDraft, send, stop, clear } = agent;
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus, enabled: opened });
  const presets = usePresets();
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const noKey = agent.noKey || secrets.data?.ai_api_key === false;

  useEffect(() => {
    const list = listRef.current;
    if (list !== null) list.scrollTop = list.scrollHeight;
  }, [entries]);

  const submit = () => {
    if (busy || draft.trim() === "") return;
    send(draft);
    setDraft("");
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };
  const fill = (text: string) => {
    setDraft(text);
    inputRef.current?.focus();
  };

  return (
    <Drawer
      opened={opened}
      onClose={close}
      position="right"
      size={440}
      withOverlay={false}
      lockScroll={false}
      trapFocus={false}
      closeOnClickOutside={false}
      title={
        <Group gap={8}>
          <IconSparkle size={18} />
          <Text fw={700}>AI 助手</Text>
        </Group>
      }
      styles={{
        content: { background: "var(--mo-side)", display: "flex", flexDirection: "column" },
        header: { background: "var(--mo-side)" },
        body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" },
      }}
    >
      <div className={styles.body}>
        {noKey && (
          <Alert color="yellow" variant="light" p="xs">
            {NO_KEY}。
            <Anchor component={Link} to="/settings?section=ai" size="sm" fw={600}>
              去设置
            </Anchor>
          </Alert>
        )}
        <div className={styles.list} ref={listRef} aria-live="polite">
          {entries.length === 0 ? (
            <Stack gap="xs" mt="md">
              <Text size="sm" c="var(--mo-dim)">
                可以让我查找、播放、改字段、刮削或扫描。一次改两个及以上条目时会先请你批准。
              </Text>
              <Group gap="xs">
                {presets.map((text) => (
                  <Button key={text} size="xs" variant="default" onClick={() => fill(text)}>
                    {text}
                  </Button>
                ))}
              </Group>
            </Stack>
          ) : (
            entries.map((entry) => <EntryView key={entry.key} entry={entry} />)
          )}
          {busy && entries[entries.length - 1]?.kind !== "assistant" && <Loader size="xs" type="dots" />}
        </div>
        <div className={styles.composer}>
          <Textarea
            ref={inputRef}
            aria-label="给 AI 的消息"
            placeholder="问点什么，回车发送，Shift+回车换行"
            autosize
            minRows={2}
            maxRows={8}
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
            onKeyDown={onKeyDown}
          />
          <Group justify="space-between" mt="xs">
            <Button size="xs" variant="subtle" color="gray" onClick={clear} disabled={entries.length === 0}>
              清空对话
            </Button>
            {busy ? (
              <Button size="xs" color="red" variant="light" onClick={stop}>
                停止
              </Button>
            ) : (
              <Button size="xs" onClick={submit} disabled={draft.trim() === ""}>
                发送
              </Button>
            )}
          </Group>
        </div>
      </div>
    </Drawer>
  );
}
