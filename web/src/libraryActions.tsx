import { Button, Group } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type Library, type TaskDetail, organizeLibrary, scrapeLibrary } from "./api";
import { useTaskWatcher } from "./useTaskWatcher";

type Kind = "organize" | "scrape";

function summary(task: TaskDetail): string {
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
      parts.push("没有填 TMDB API Key，只扫描了文件");
    }
    return parts.join("，");
  }
  const queued = Number(result.scrape_queued ?? 0);
  return queued === 0 ? "没有要刮削的电影" : `刮削了 ${queued} 部`;
}

export function LibraryActions({ library }: { library: Library }) {
  const queryClient = useQueryClient();
  const [running, setRunning] = useState<Kind | null>(null);
  const watcher = useTaskWatcher((task) => {
    setRunning(null);
    void queryClient.invalidateQueries({ queryKey: ["libraries"] });
    void queryClient.invalidateQueries({ queryKey: ["library", library.id] });
    void queryClient.invalidateQueries({ queryKey: ["wall", library.id] });
    void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
    const failed = task.child_status.failed ?? 0;
    notifications.show({
      color: task.status === "succeeded" && failed === 0 ? "green" : "red",
      title: `「${library.name}」${task.task_type === "organize" ? "扫描" : "刮削"}结束`,
      message:
        failed > 0
          ? `${summary(task)}；有 ${String(failed)} 部刮削失败，详情看任务页`
          : summary(task),
    });
  });
  const start = useMutation({
    mutationFn: (kind: Kind) =>
      kind === "organize" ? organizeLibrary(library.id) : scrapeLibrary(library.id),
    onMutate: (kind) => setRunning(kind),
    onSuccess: ({ task_id }, kind) => {
      watcher.watch(task_id);
      notifications.show({
        message:
          kind === "scrape"
            ? "已开始刮削还没刮削过的电影"
            : library.is_auto_scrape
              ? "已开始扫描，扫完会接着刮削"
              : "已开始扫描",
      });
    },
    onError: (error) => {
      setRunning(null);
      notifications.show({ color: "red", message: error.message });
    },
  });
  return (
    <Group gap="xs" wrap="nowrap">
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
    </Group>
  );
}
