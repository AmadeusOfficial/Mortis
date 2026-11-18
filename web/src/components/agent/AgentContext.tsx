import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api";
import { type ApprovalItem, type ChatMessage, type ChatRequest, streamChat } from "./chatStream";

export const NO_KEY = "先在设置里填写 AI 的 key";

export type Entry =
  | { kind: "user"; key: string; text: string }
  | { kind: "assistant"; key: string; text: string }
  | { kind: "tool"; key: string; callId: string; name: string; arguments: unknown; result?: unknown }
  | {
      kind: "approval";
      key: string;
      approvalId: string;
      summary: string;
      items: ApprovalItem[];
      status: "pending" | "approved" | "rejected";
    }
  | { kind: "error"; key: string; message: string };

interface AgentState {
  opened: boolean;
  open: () => void;
  close: () => void;
  /** 打开抽屉并把文字填进输入框，不发送。 */
  openWithDraft: (text: string) => void;
  draft: string;
  setDraft: (text: string) => void;
  entries: Entry[];
  busy: boolean;
  noKey: boolean;
  send: (text: string) => void;
  decide: (approvalId: string, approved: boolean) => void;
  stop: () => void;
  clear: () => void;
}

const AgentContext = createContext<AgentState | null>(null);

let counter = 0;
const nextKey = () => `e${++counter}`;

export function AgentProvider({ children }: { children: ReactNode }) {
  const [opened, setOpened] = useState(false);
  const [draft, setDraft] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const [noKey, setNoKey] = useState(false);
  // 下一次请求要带上的完整历史。只在收到 messages 事件或被停止时更新。
  const history = useRef<ChatMessage[]>([]);
  const controller = useRef<AbortController | null>(null);

  const push = useCallback((entry: Entry) => setEntries((rows) => [...rows, entry]), []);

  const run = useCallback(
    async (request: ChatRequest) => {
      const abort = new AbortController();
      controller.current = abort;
      setBusy(true);
      let finished = false;
      let partial = "";
      const appendText = (delta: string) => {
        partial += delta;
        setEntries((rows) => {
          const last = rows[rows.length - 1];
          if (last !== undefined && last.kind === "assistant") {
            return [...rows.slice(0, -1), { ...last, text: last.text + delta }];
          }
          return [...rows, { kind: "assistant", key: nextKey(), text: delta }];
        });
      };
      try {
        for await (const event of streamChat(request, abort.signal)) {
          switch (event.event) {
            case "text":
              appendText(event.data.delta);
              break;
            case "tool":
              partial = "";
              push({
                kind: "tool",
                key: nextKey(),
                callId: event.data.id,
                name: event.data.name,
                arguments: event.data.arguments,
              });
              break;
            case "tool_result":
              setEntries((rows) =>
                rows.map((row) =>
                  row.kind === "tool" && row.callId === event.data.id ? { ...row, result: event.data.result } : row,
                ),
              );
              break;
            case "approval":
              push({
                kind: "approval",
                key: nextKey(),
                approvalId: event.data.id,
                summary: event.data.summary,
                items: event.data.items,
                status: "pending",
              });
              break;
            case "messages":
              history.current = [...request.messages, ...event.data.messages];
              finished = true;
              break;
            case "error":
              if (event.data.message === NO_KEY) setNoKey(true);
              push({ kind: "error", key: nextKey(), message: event.data.message });
              break;
            case "done":
              finished = true;
              break;
          }
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          const message = error instanceof ApiError || error instanceof Error ? error.message : String(error);
          push({ kind: "error", key: nextKey(), message });
        }
      } finally {
        // 清空对话后旧请求的收尾不再改历史。
        if (controller.current === abort) {
          if (!finished) {
            // 被停止或断开：已显示的文字留下，未完成的工具调用不进历史。
            const kept: ChatMessage[] = partial ? [{ role: "assistant", content: partial }] : [];
            history.current = [...request.messages, ...kept];
          }
          controller.current = null;
          setBusy(false);
        }
      }
    },
    [push],
  );

  const send = useCallback(
    (text: string) => {
      const content = text.trim();
      if (content === "" || controller.current !== null) return;
      push({ kind: "user", key: nextKey(), text: content });
      void run({ messages: [...history.current, { role: "user", content }] });
    },
    [push, run],
  );

  const decide = useCallback(
    (approvalId: string, approved: boolean) => {
      if (controller.current !== null) return;
      setEntries((rows) =>
        rows.map((row) =>
          row.kind === "approval" && row.approvalId === approvalId
            ? { ...row, status: approved ? "approved" : "rejected" }
            : row,
        ),
      );
      void run({ messages: history.current, approval: { id: approvalId, approved } });
    },
    [run],
  );

  const stop = useCallback(() => controller.current?.abort(), []);

  const clear = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    history.current = [];
    setBusy(false);
    setEntries([]);
    setNoKey(false);
  }, []);

  const value = useMemo<AgentState>(
    () => ({
      opened,
      open: () => setOpened(true),
      close: () => setOpened(false),
      openWithDraft: (text: string) => {
        setDraft(text);
        setOpened(true);
      },
      draft,
      setDraft,
      entries,
      busy,
      noKey,
      send,
      decide,
      stop,
      clear,
    }),
    [opened, draft, entries, busy, noKey, send, decide, stop, clear],
  );
  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>;
}

export function useAgent(): AgentState {
  const value = useContext(AgentContext);
  if (value === null) throw new Error("useAgent 要放在 AgentProvider 里");
  return value;
}
