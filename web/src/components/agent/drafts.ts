import type { FileGuess, ItemDetail, UnmatchedFile } from "../../api";
import type { Candidate } from "./chatStream";

export const ASK_CANDIDATES = "请找出正确的作品，列候选给我选。";

function yearText(year: number | null | undefined): string {
  return year === null || year === undefined ? "年份未知" : String(year);
}

function sourceLabel(source: string, name?: string): string {
  if (source === "tmdb") return "TMDB";
  return name ?? source;
}

function fileLines(file: { file_name: string; folder_name: string; title: string; year: number | null }) {
  return [
    `文件名：${file.file_name}`,
    `文件夹：${file.folder_name}`,
    `从文件名解析出：标题「${file.title}」，${file.year === null ? "年份未知" : `年份 ${file.year}`}`,
  ];
}

function currentMatch(item: Pick<ItemDetail, "title" | "year" | "external_ids">): string {
  const ids = item.external_ids;
  const provider = "tmdb" in ids ? "tmdb" : Object.keys(ids)[0];
  const id = provider === undefined ? "没有外部编号" : `${sourceLabel(provider)} ${ids[provider]}`;
  return `现在匹配的是：《${item.title}》（${yearText(item.year)}，${id}）`;
}

/** 详情页「让 AI 重新识别」填进输入框的话。 */
export function rematchDraft(
  item: Pick<ItemDetail, "id" | "title" | "year" | "external_ids"> & { guess?: FileGuess | null },
): string {
  const guess = item.guess ?? null;
  return [
    `帮我重新识别条目 ${item.id}。`,
    ...(guess === null ? ["没有关联的文件。"] : fileLines(guess)),
    currentMatch(item),
    ASK_CANDIDATES,
  ].join("\n");
}

/** 未识别页「让 AI 识别」填进输入框的话。 */
export function bindDraft(row: Pick<UnmatchedFile, "id" | "path" | "guess_title" | "guess_year">): string {
  const parts = row.path.split(/[\\/]/).filter((part) => part !== "");
  return [
    `帮我识别未识别的文件 ${row.id}。`,
    ...fileLines({
      file_name: parts[parts.length - 1] ?? row.path,
      folder_name: parts[parts.length - 2] ?? "",
      title: row.guess_title,
      year: row.guess_year,
    }),
    ASK_CANDIDATES,
  ].join("\n");
}

/** 点候选卡片发出的话，序号从 1 开始。 */
export function pickMessage(index: number, candidate: Candidate): string {
  const source = sourceLabel(candidate.source, candidate.source_name);
  return `选第 ${index + 1} 个：《${candidate.title}》（${yearText(candidate.year)}，${source} ${candidate.external_id}）`;
}

export function targetText(target: {
  source: string;
  external_id: string;
  title?: string | null;
  year?: number | null;
}): string {
  const id = `${sourceLabel(target.source)} ${target.external_id}`;
  if (!target.title) return id;
  return `《${target.title}》（${yearText(target.year)}，${id}）`;
}

export function idsText(ids: Record<string, string> | undefined): string {
  const entries = Object.entries(ids ?? {});
  if (entries.length === 0) return "没有外部编号";
  return entries.map(([provider, id]) => `${sourceLabel(provider)} ${id}`).join("、");
}
