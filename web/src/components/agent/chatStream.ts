import { ApiError } from "../../api";

export interface ChatToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

/** 发给后端的消息，OpenAI 格式。system 由后端加。 */
export type ChatMessage =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string | null; tool_calls?: ChatToolCall[] }
  | { role: "tool"; tool_call_id: string; content: string };

export interface SearchRow {
  id: number;
  title: string;
  original_title: string | null;
  year: number | null;
  library_id: number;
  library_name: string;
  data_type: string;
  kind: string;
  structure: string;
  poster_url: string | null;
}

/** search_candidates 的一个外部候选。 */
export interface Candidate {
  source: string;
  source_name?: string;
  external_id: string;
  title: string;
  original_title: string | null;
  year: number | null;
  poster_url: string | null;
  overview: string | null;
}

/** 批准卡片上要换成的作品；拿不到标题时只有来源和编号。 */
export interface MatchTarget {
  source: string;
  external_id: string;
  title?: string | null;
  original_title?: string | null;
  year?: number | null;
}

export interface ApprovalItem {
  tool: string;
  item_id?: number;
  file_id?: number;
  title?: string;
  values?: Record<string, unknown>;
  fields?: { key: string; label: string; value: unknown }[];
  current?: {
    title?: string;
    year?: number | null;
    external_ids?: Record<string, string>;
    file_name?: string;
    guess_title?: string;
    guess_year?: number | null;
  };
  target?: MatchTarget;
  note?: string;
  error?: string;
  arguments?: Record<string, unknown>;
}

export type ChatEvent =
  | { event: "text"; data: { delta: string } }
  | { event: "tool"; data: { id: string; name: string; arguments: unknown } }
  | { event: "tool_result"; data: { id: string; name: string; result: unknown } }
  | { event: "approval"; data: { id: string; summary: string; items: ApprovalItem[] } }
  | { event: "messages"; data: { messages: ChatMessage[] } }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: Record<string, never> };

export interface ChatRequest {
  messages: ChatMessage[];
  approval?: { id: string; approved: boolean };
}

function parseBlock(block: string): ChatEvent | null {
  let name = "";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (name === "" || data.length === 0) return null;
  try {
    return { event: name, data: JSON.parse(data.join("\n")) } as ChatEvent;
  } catch {
    return null;
  }
}

async function failure(response: Response): Promise<ApiError> {
  let message = `请求失败：HTTP ${response.status}`;
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = body.detail;
      if (typeof detail === "string") message = detail;
      else if (Array.isArray(detail)) {
        message = detail
          .map((entry: unknown) =>
            typeof entry === "object" && entry !== null && "msg" in entry ? String(entry.msg) : String(entry),
          )
          .join("；");
      }
    }
  } catch {
    // 不是 JSON 时用状态码。
  }
  return new ApiError(message, response.status);
}

/** POST /api/agent/chat，逐个交出 SSE 事件。中断用 signal。 */
export async function* streamChat(body: ChatRequest, signal: AbortSignal): AsyncGenerator<ChatEvent> {
  let response: Response;
  try {
    response = await fetch("/api/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof TypeError) throw new ApiError("连不上后端", 0);
    throw error;
  }
  if (!response.ok || response.body === null) throw await failure(response);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let cut = buffer.search(/\r?\n\r?\n/);
      while (cut >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut).replace(/^\r?\n\r?\n/, "");
        const event = parseBlock(block);
        if (event !== null) yield event;
        cut = buffer.search(/\r?\n\r?\n/);
      }
    }
    const rest = parseBlock(buffer);
    if (rest !== null) yield rest;
  } finally {
    reader.releaseLock();
  }
}
