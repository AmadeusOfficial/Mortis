import { Button, Group } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type Library,
  type TaskDetail,
  fetchLibraryRenamePreview,
  organizeLibrary,
  renameLibrary,
  scrapeLibrary,
  writeLibraryFiles,
} from "./api";
import { RenamePreviewModal } from "./components/RenamePreviewModal";
import { useTaskWatcher } from "./useTaskWatcher";

type Kind = "organize" | "scrape" | "write" | "rename";

const START_MESSAGES: Record<Kind, string> = {
  organize: "已开始扫描",
  scrape: "已开始刮削还没刮削过的电影",
  write: "已开始写出整库文件",
  rename: "已开始重命名整库",
};

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
  if (task.task_type === "write_files_library" || task.task_type === "rename_library") {
    return `排了 ${String(result.queued ?? 0)} 部`;
  }
  const queued = Number(result.scrape_queued ?? 0);
  return queued === 0 ? "没有要刮削的电影" : `刮削了 ${queued} 部`;
}

export function LibraryActions({ library }: { library: Library }) {
  const queryClient = useQueryClient();
  const [running, setRunning] = useState<Kind | null>(null);
  const [renaming, setRenaming] = useState(false);
  const watcher = useTaskWatcher((task) => {
    setRunning(null);
    void queryClient.invalidateQueries({ queryKey: ["libraries"] });
    void queryClient.invalidateQueries({ queryKey: ["library", library.id] });
    void queryClient.invalidateQueries({ queryKey: ["wall", library.id] });
    void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
    void queryClient.invalidateQueries({ queryKey: ["library-rename-preview", library.id] });
    const failed = task.child_status.failed ?? 0;
    notifications.show({
      color: task.status === "succeeded" && failed === 0 ? "green" : "red",
      title: `「${library.name}」${task.type_label}结束`,
      message:
        failed > 0
          ? `${summary(task)}；有 ${String(failed)} 部失败，详情看任务页`
          : summary(task),
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
      if (kind === "write") {
        return writeLibraryFiles(library.id);
      }
      return renameLibrary(library.id);
    },
    onMutate: (kind) => setRunning(kind),
    onSuccess: ({ task_id }, kind) => {
      if (kind === "rename") {
        setRenaming(false);
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
      if (kind === "rename") {
        return;
      }
      notifications.show({ color: "red", message: error.message });
    },
  });
  const preview = useQuery({
    queryKey: ["library-rename-preview", library.id],
    queryFn: () => fetchLibraryRenamePreview(library.id),
    enabled: renaming,
    retry: false,
  });
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
        <Button
          size="xs"
          variant="light"
          loading={running === "write"}
          disabled={running !== null}
          onClick={() => start.mutate("write")}
        >
          写出整库文件
        </Button>
        <Button
          size="xs"
          variant="light"
          loading={running === "rename"}
          disabled={running !== null}
          onClick={() => {
            start.reset();
            setRenaming(true);
          }}
        >
          重命名整库
        </Button>
      </Group>
      <RenamePreviewModal
        opened={renaming}
        onClose={() => setRenaming(false)}
        previews={preview.data}
        loading={preview.isFetching}
        error={
          preview.isError
            ? preview.error.message
            : start.isError && running === null
              ? start.error.message
              : null
        }
        onConfirm={() => start.mutate("rename")}
        confirming={start.isPending}
      />
    </>
  );
}
