import { Alert, Anchor, Button, Drawer, Group, Loader, Stack, Text, Textarea, UnstyledButton } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import { Link, useLocation } from "react-router";
import { fetchItem, fetchLibraries, fetchSecretsStatus } from "../../api";
import { IconChevronDown, IconChevronRight, IconSparkle } from "../../icons";
import { Pill } from "../ui";
import { type Entry, NO_KEY, useAgent } from "./AgentContext";
import styles from "./AgentDrawer.module.css";
import type { ApprovalItem, Candidate, SearchRow } from "./chatStream";
import { idsText, pickMessage, rematchDraft, targetText } from "./drafts";

const TOOL_LABELS: Record<string, string> = {
  search_items: "检索",
  get_item: "查看",
  update_fields: "修改",
  set_user_tags: "改标签",
  scrape_item: "刮削",
  scan_library: "扫描",
  play_item: "播放",
  search_candidates: "搜候选",
  list_unmatched: "列未识别",
  rematch_item: "重新匹配",
  bind_file: "绑定文件",
};

const markdown: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  // 回答里的图片不加载，免得模型把片库数据拼进地址发到外面；只显示成文字。
  img: ({ alt, src }) => (
    <span className={styles.image}>
      [图片{alt ? `：${alt}` : ""}] {typeof src === "string" ? src : ""}
    </span>
  ),
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function toolTarget(name: string, args: Record<string, unknown>, result: Record<string, unknown>): string {
  if (name === "search_items") return String(args.query ?? "");
  if (name === "search_candidates") return String(result.query ?? args.query ?? "");
  if (name === "list_unmatched") return "";
  if (args.file_id !== undefined && typeof result.title !== "string") return `文件 ${String(args.file_id)}`;
  if (name === "scan_library") {
    return typeof result.library_name === "string" ? result.library_name : `库 ${String(args.library_id ?? "")}`;
  }
  if (typeof result.title === "string") return result.title;
  return args.item_id !== undefined ? `条目 ${String(args.item_id)}` : "";
}

function searchRows(result: Record<string, unknown>): SearchRow[] {
  return Array.isArray(result.items) ? (result.items as SearchRow[]) : [];
}

function candidateRows(result: Record<string, unknown>): Candidate[] {
  return Array.isArray(result.candidates) ? (result.candidates as Candidate[]) : [];
}

function Poster({ url }: { url: string | null }) {
  return url ? <img className={styles.poster} src={url} alt="" loading="lazy" /> : <div className={styles.poster} />;
}

function CandidateCards({ candidates }: { candidates: Candidate[] }) {
  const { send, busy } = useAgent();
  return (
    <div className={styles.cards} role="list" aria-label="候选">
      {candidates.map((candidate, index) => (
        <button
          key={`${candidate.source}:${candidate.external_id}`}
          type="button"
          role="listitem"
          className={styles.card}
          disabled={busy}
          onClick={() => send(pickMessage(index, candidate))}
        >
          <span className={styles.number}>{index + 1}</span>
          <Poster url={candidate.poster_url} />
          <div className={styles.cardText}>
            <Text size="sm" fw={600} truncate>
              {candidate.title}
              {candidate.year !== null && ` (${candidate.year})`}
            </Text>
            {candidate.original_title && candidate.original_title !== candidate.title && (
              <Text size="xs" c="var(--mo-dim)" truncate>
                {candidate.original_title}
              </Text>
            )}
            <Text size="xs" c="var(--mo-muted)" className="mo-mono">
              {candidate.source === "tmdb" ? "TMDB" : (candidate.source_name ?? candidate.source)}{" "}
              {candidate.external_id}
            </Text>
            {candidate.overview && (
              <Text size="xs" c="var(--mo-dim)" lineClamp={2}>
                {candidate.overview}
              </Text>
            )}
          </div>
        </button>
      ))}
    </div>
  );
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
  const candidates = entry.name === "search_candidates" ? candidateRows(result) : [];
  const counted = entry.name === "search_items" || entry.name === "search_candidates";
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
        {counted && !pending && error === null && (
          <Text span size="xs" c="var(--mo-muted)">
            {entry.name === "search_items" ? `${rows.length} 条` : `${candidates.length} 个候选`}
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
              <Poster url={row.poster_url ?? null} />
              <div className={styles.cardText}>
                <Text size="sm" fw={600} truncate>
                  {row.title}
                </Text>
                <Text size="xs" c="var(--mo-dim)" truncate>
                  {[row.year, row.library_name, row.structure]
                    .filter((part) => part !== null && part !== "")
                    .join(" · ")}
                </Text>
              </div>
            </Link>
          ))}
        </div>
      )}
      {candidates.length > 0 && <CandidateCards candidates={candidates} />}
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
    const fields =
      item.fields ?? Object.entries(item.values ?? {}).map(([key, value]) => ({ key, label: key, value }));
    return (
      <div>
        <Text size="sm" fw={600}>
          {item.title}
        </Text>
        {fields.map((field) => (
          <Text key={field.key} size="xs" c="var(--mo-dim)">
            {field.label} → {formatValue(field.value)}
          </Text>
        ))}
      </div>
    );
  }
  if (item.tool === "set_user_tags" && item.title !== undefined) {
    const changes = [
      item.add !== undefined && item.add.length > 0 ? `加上：${item.add.join("、")}` : null,
      item.remove !== undefined && item.remove.length > 0 ? `去掉：${item.remove.join("、")}` : null,
    ].filter((part) => part !== null);
    return (
      <div>
        <Text size="sm" fw={600}>
          {item.title}
        </Text>
        <Text size="xs" c="var(--mo-dim)">
          {changes.join("／")}
        </Text>
      </div>
    );
  }
  if ((item.tool === "rematch_item" || item.tool === "bind_file") && item.target !== undefined) {
    const current = item.current ?? {};
    const now =
      item.tool === "rematch_item"
        ? `《${current.title ?? item.title ?? ""}》（${current.year ?? "年份未知"}，${idsText(current.external_ids)}）`
        : `未识别：${current.file_name ?? item.title ?? ""}`;
    return (
      <div>
        <Text size="sm" fw={600}>
          {TOOL_LABELS[item.tool]}：{item.title}
        </Text>
        <div className={styles.change}>
          <Text span size="xs" c="var(--mo-muted)">
            现在
          </Text>
          <Text span size="xs">
            {now}
          </Text>
          <Text span size="xs" c="var(--mo-muted)">
            换成
          </Text>
          <Text span size="xs" fw={600}>
            {targetText(item.target)}
          </Text>
        </div>
        {item.note && (
          <Text size="xs" c="var(--mo-dim)" mt={2}>
            {item.note}
          </Text>
        )}
      </div>
    );
  }
  return (
    <Text size="xs" className="mo-mono">
      {TOOL_LABELS[item.tool] ?? item.tool} {JSON.stringify(item.arguments ?? {})}
    </Text>
  );
}

const APPROVAL_STATUS = { approved: "已批准", rejected: "已拒绝", expired: "已失效" } as const;

function ReasoningEntry({ entry }: { entry: Extract<Entry, { kind: "reasoning" }> }) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (entry.done) setExpanded(false);
  }, [entry.done]);
  return (
    <div className={styles.reasoning}>
      <UnstyledButton
        className={styles.reasoningLine}
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        {expanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
        <span>{entry.done ? "思考过程" : "思考中…"}</span>
      </UnstyledButton>
      {expanded && <div className={styles.reasoningText}>{entry.text}</div>}
    </div>
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
          {APPROVAL_STATUS[entry.status]}
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
    case "reasoning":
      return <ReasoningEntry entry={entry} />;
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

interface Preset {
  label: string;
  text: string;
}

function preset(text: string): Preset {
  return { label: text, text };
}

/** 抽屉为空时按页面给的预设问题，点了填进输入框，不发送。 */
function usePresets(): Preset[] {
  const { pathname } = useLocation();
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const itemMatch = /^\/items\/(\d+)/.exec(pathname);
  const itemId = itemMatch ? Number(itemMatch[1]) : null;
  const item = useQuery({
    queryKey: ["item", itemId],
    queryFn: () => fetchItem(itemId ?? 0),
    enabled: itemId !== null,
  });
  if (itemId !== null) {
    const detail = item.data;
    return detail?.kind === "work"
      ? [{ label: "让 AI 重新识别这部", text: rematchDraft(detail) }, preset("播放")]
      : [preset("播放")];
  }
  if (pathname.startsWith("/unmatched")) return [preset("帮我识别这些未识别的文件")];
  const match = /^\/libraries\/(\d+)/.exec(pathname);
  const library = match ? libraries.data?.find((row) => row.id === Number(match[1])) : undefined;
  return [preset(library ? `「${library.name}」里有什么？` : "库里都有些什么？"), preset("播放")];
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
                可以让我查找、播放、改字段、刮削、扫描或纠正识别。一次改两个及以上条目、换匹配或绑文件时会先请你批准。
              </Text>
              <Group gap="xs">
                {presets.map((item) => (
                  <Button key={item.label} size="xs" variant="default" onClick={() => fill(item.text)}>
                    {item.label}
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
