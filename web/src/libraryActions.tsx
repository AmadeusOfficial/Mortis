import { Button, Divider, Group } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type Library,
  type TaskDetail,
  arrangeLibrary,
  fetchLibraryArrangePreview,
  organizeLibrary,
  scrapeLibrary,
} from "./api";
import { ArrangePreviewModal } from "./components/ArrangePreviewModal";
import { isPluginType, useDataTypes } from "./dataTypes";
import { useTaskWatcher } from "./useTaskWatcher";

type Kind = "organize" | "scrape" | "arrange";

const START_MESSAGES: Record<Kind, string> = {
  organize: "已开始扫描",
  scrape: "已开始刮削还没刮削过的条目",
  arrange: "已开始整理整库",
};

const DONE_LABELS: Record<string, string> = {
  organize: "扫描",
  scrape_library: "刮削",
  arrange_library: "整理",
};

function summary(task: TaskDetail, pluginType: boolean): string {
  if (task.status !== "succeeded") {
    return task.error ?? "任务没有完成";
  }
  const result = task.result ?? {};
  if (task.task_type === "organize") {
    const parts = [`找到 ${String(result.found ?? 0)} 部`, `认出 ${String(result.matched ?? 0)} 部`];
    if (Number(result.unmatched ?? 0) > 0) {
      parts.push(`${String(result.unmatched)} 部没认出，可在「未识别」里手动匹配`);
    }
    if (result.identified === false) {
      parts.push(
        pluginType ? "没有启用的来源插件，只扫描了文件" : "没有填 TMDB API Key，只扫描了文件",
      );
    }
    return parts.join("，");
  }
  if (task.task_type === "arrange_library") {
    return `整理了 ${String(result.queued ?? 0)} 部`;
  }
  const queued = Number(result.scrape_queued ?? 0);
  return queued === 0 ? "没有要刮削的电影" : `刮削了 ${queued} 部`;
}

export function LibraryActions({ library }: { library: Library }) {
  const queryClient = useQueryClient();
  const [running, setRunning] = useState<Kind | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const dataTypes = useDataTypes();
  // 插件类型不写 NFO、不重命名，没有整理。
  const canArrange = !isPluginType(dataTypes.data, library.data_type_id);
  const watcher = useTaskWatcher((task) => {
    setRunning(null);
    void queryClient.invalidateQueries({ queryKey: ["libraries"] });
    void queryClient.invalidateQueries({ queryKey: ["library", library.id] });
    void queryClient.invalidateQueries({ queryKey: ["wall", library.id] });
    void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
    void queryClient.invalidateQueries({ queryKey: ["library-arrange-preview", library.id] });
    const failed = task.tree_status.failed ?? 0;
    notifications.show({
      color: task.status === "succeeded" && failed === 0 ? "green" : "red",
      title: `「${library.name}」${DONE_LABELS[task.task_type] ?? task.type_label}结束`,
      message:
        failed > 0
          ? `${summary(task, !canArrange)}；有 ${String(failed)} 部失败，详情看任务页`
          : summary(task, !canArrange),
    });
  });
  const start = useMutation({
    mutationFn: (kind: Kind) => {
      if (kind === "organize") {
        return organizeLibrary(library.id);
      }
      if (kind === "scrape") {
        return scrapeLibrary(library.id);
      }
      return arrangeLibrary(library.id);
    },
    onMutate: (kind) => setRunning(kind),
    onSuccess: ({ task_id }, kind) => {
      if (kind === "arrange") {
        setPreviewing(false);
      }
      watcher.watch(task_id);
      notifications.show({
        message:
          kind === "organize" && library.is_auto_scrape
            ? "已开始扫描，扫完会接着刮削"
            : START_MESSAGES[kind],
      });
    },
    onError: (error, kind) => {
      setRunning(null);
      if (kind === "arrange") {
        return;
      }
      notifications.show({ color: "red", message: error.message });
    },
  });
  const preview = useQuery({
    queryKey: ["library-arrange-preview", library.id],
    queryFn: () => fetchLibraryArrangePreview(library.id),
    enabled: previewing,
    retry: false,
  });
  const plan = preview.data;
  return (
    <>
      <Group gap="xs" wrap="wrap">
        <Button
          size="xs"
          loading={running === "organize"}
          disabled={running !== null}
          onClick={() => start.mutate("organize")}
        >
          扫描
        </Button>
        <Button
          size="xs"
          variant="light"
          loading={running === "scrape"}
          disabled={running !== null}
          onClick={() => start.mutate("scrape")}
        >
          刮削未刮削的
        </Button>
        {canArrange && (
          <>
            <Divider orientation="vertical" />
            <Button
              size="xs"
              variant="light"
              loading={running === "arrange"}
              disabled={running !== null}
              onClick={() => {
                start.reset();
                setPreviewing(true);
              }}
            >
              整理整库
            </Button>
          </>
        )}
      </Group>
      <ArrangePreviewModal
        opened={previewing}
        onClose={() => setPreviewing(false)}
        plan={
          plan === undefined
            ? undefined
            : {
                rename: plan.rename,
                write: plan.write,
                scope: `整理刮削过的 ${String(plan.count)} 部电影`,
                empty: plan.count === 0 ? "这个库还没有刮削过的电影，先刮削再整理。" : null,
                previews: plan.previews,
                canConfirm: plan.count > 0 && (plan.rename || plan.write),
              }
        }
        loading={preview.isFetching}
        error={
          preview.isError
            ? preview.error.message
            : start.isError && running === null
              ? start.error.message
              : null
        }
        onConfirm={() => start.mutate("arrange")}
        confirming={start.isPending}
      />
    </>
  );
}
