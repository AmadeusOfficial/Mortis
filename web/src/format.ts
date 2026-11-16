export function formatTime(iso: string | null): string {
  if (iso === null) {
    return "—";
  }
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

export function formatSize(bytes: number | null): string {
  if (bytes === null) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** 从开始到结束的耗时；没开始或没结束时返回空字符串。 */
export function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (startedAt === null || finishedAt === null) {
    return "";
  }
  const seconds = Math.max(
    0,
    Math.round((new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000),
  );
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} 分 ${seconds % 60} 秒`;
  }
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}
