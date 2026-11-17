import { Checkbox, Radio, Select, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type Automation, type Library, previewCron } from "../../api";

export const CRON_PRESETS = [
  { value: "0 3 * * *", label: "每天凌晨 3 点" },
  { value: "0 */6 * * *", label: "每 6 小时" },
  { value: "0 3 * * 1", label: "每周一凌晨 3 点" },
];
const CUSTOM = "custom";
export const DEFAULT_CRON = CRON_PRESETS[0].value;

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

// 按本机时间写成「今天 03:00」「明天 03:00」「11月23日 周一 03:00」。
export function formatRunTime(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) {
    return iso;
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  const time = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (sameDay(at, now)) {
    return `今天 ${time}`;
  }
  if (sameDay(at, tomorrow)) {
    return `明天 ${time}`;
  }
  const year = at.getFullYear() === now.getFullYear() ? "" : `${at.getFullYear()}年`;
  return `${year}${at.getMonth() + 1}月${at.getDate()}日 ${WEEKDAYS[at.getDay()]} ${time}`;
}

export interface AutomationSummary {
  text: string;
  tone: "dim" | "ok" | "bad";
  title?: string;
}

// 卡片和海报墙页头上的扫描方式说明。
export function automationSummary(library: Library): AutomationSummary {
  if (library.automation === "schedule") {
    return library.next_scan_at === null
      ? { text: "定时扫描", tone: "dim" }
      : {
          text: `下次扫描 ${formatRunTime(library.next_scan_at)}`,
          tone: "dim",
          title: library.schedule_cron ?? undefined,
        };
  }
  if (library.automation === "watch") {
    if (library.watch_state === "failed") {
      const text = `监听失败：${library.watch_error ?? "原因不明"}`;
      return { text, tone: "bad", title: `${text}。每 10 分钟重试打开一次。` };
    }
    if (library.watch_state === "watching") {
      return { text: "正在监听", tone: "ok", title: "目录里有视频或字幕变动，停 30 秒后扫描" };
    }
    return { text: "发现变动就扫描", tone: "dim" };
  }
  return { text: "只手动扫描", tone: "dim" };
}

export interface AutomationValue {
  automation: Automation;
  cron: string;
  refreshRunning: boolean;
}

export function automationValue(library: Library | null): AutomationValue {
  return {
    automation: library?.automation ?? "manual",
    cron: library?.schedule_cron ?? DEFAULT_CRON,
    refreshRunning: library?.refresh_running ?? false,
  };
}

function CronPreviewLine({ cron }: { cron: string }) {
  const [debounced] = useDebouncedValue(cron.trim(), 300);
  const preview = useQuery({
    queryKey: ["cron-preview", debounced],
    queryFn: () => previewCron(debounced),
    enabled: debounced !== "",
    retry: false,
  });
  if (debounced === "") {
    return (
      <Text size="sm" c="red">
        规则不能为空
      </Text>
    );
  }
  if (preview.isError) {
    return (
      <Text size="sm" c="red" aria-label="定时规则错误">
        规则不对：{preview.error.message}
      </Text>
    );
  }
  if (preview.data === undefined) {
    return (
      <Text size="sm" c="dimmed">
        正在计算下次扫描时间…
      </Text>
    );
  }
  const [first, ...rest] = preview.data.next_runs;
  return (
    <Text size="sm" c="dimmed" aria-label="下次扫描时间">
      下次扫描 {formatRunTime(first)}
      {rest.length > 0 && `，之后 ${rest.map((run) => formatRunTime(run)).join("、")}`}
    </Text>
  );
}

export function AutomationFields({
  value,
  onChange,
  seriesType,
}: {
  value: AutomationValue;
  onChange: (value: AutomationValue) => void;
  seriesType: boolean;
}) {
  const [custom, setCustom] = useState(
    () => !CRON_PRESETS.some((item) => item.value === value.cron),
  );
  const preset = custom ? CUSTOM : value.cron;
  return (
    <Stack gap="xs">
      <Radio.Group
        label="扫描方式"
        value={value.automation}
        onChange={(next) => onChange({ ...value, automation: next as Automation })}
      >
        <Stack gap={8} mt={6}>
          <Radio value="manual" label="只手动扫描" description="在卡片上点「扫描」才扫描" />
          <Radio
            value="watch"
            label="发现变动就扫描"
            description="目录里新增、删除或改名视频和字幕后，停 30 秒没有新变动就扫描一次"
          />
          <Radio value="schedule" label="定时扫描" description="按设定的时间扫描" />
        </Stack>
      </Radio.Group>
      {value.automation === "schedule" && (
        <Stack gap={6} pl={30}>
          <Select
            label="什么时候扫描"
            data={[...CRON_PRESETS, { value: CUSTOM, label: "自定义 cron" }]}
            value={preset}
            allowDeselect={false}
            onChange={(next) => {
              if (next === null) {
                return;
              }
              setCustom(next === CUSTOM);
              if (next !== CUSTOM) {
                onChange({ ...value, cron: next });
              }
            }}
          />
          {preset === CUSTOM && (
            <TextInput
              label="cron 规则"
              description="分 时 日 月 周，支持 *、数字、逗号、区间和步长，例如 30 2 * * 1-5"
              value={value.cron}
              onChange={(event) => onChange({ ...value, cron: event.currentTarget.value })}
              classNames={{ input: "mo-mono" }}
            />
          )}
          <CronPreviewLine cron={value.cron} />
          {seriesType && (
            <Checkbox
              mt={4}
              label="同时刷新连载中的剧"
              description="到点时把状态是连载中的剧重新向 TMDB 请求一遍，补上新播出的集"
              checked={value.refreshRunning}
              onChange={(event) =>
                onChange({ ...value, refreshRunning: event.currentTarget.checked })
              }
            />
          )}
        </Stack>
      )}
    </Stack>
  );
}
