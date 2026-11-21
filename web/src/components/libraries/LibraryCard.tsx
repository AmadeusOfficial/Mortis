import { Button, Group, Menu, Modal, Stack, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import {
  type Library,
  type TaskDetail,
  arrangeLibrary,
  fetchLibraryArrangePreview,
  organizeLibrary,
  scrapeLibrary,
  updateLibrary,
} from "../../api";
import { ArrangePreviewModal } from "../ArrangePreviewModal";
import { EmptyFolderModal } from "./EmptyFolderModal";
import { automationSummary } from "./automation";
import {
  DataTypeIcon,
  IconEdit,
  IconFolder,
  IconMore,
  IconOrganize,
  IconRefresh,
  IconScan,
  IconScrape,
  IconTrash,
} from "../../icons";
import classes from "../../pages/LibrariesPage.module.css";
import { useTaskWatcher } from "../../useTaskWatcher";

type ActionKind = "organize" | "scrape" | "rescrape" | "arrange";

const START_MESSAGES: Record<ActionKind, string> = {
  organize: "已开始扫描",
  scrape: "已开始刮削还没刮削过的条目",
  rescrape: "已开始重新刮削全部条目",
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
    const parts = [
      `找到 ${String(result.found ?? 0)} 部`,
      `认出 ${String(result.matched ?? 0)} 部`,
    ];
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

export function LibraryCard({
  library,
  itemUnit,
  fileUnit,
  canArrange,
  onEdit,
  onDelete,
}: {
  library: Library;
  itemUnit: string;
  fileUnit: string;
  canArrange: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const queryClient = useQueryClient();
  const [running, setRunning] = useState<ActionKind | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirmingRescrape, setConfirmingRescrape] = useState(false);
  const [cleaning, setCleaning] = useState(false);

  const toggle = useMutation({
    mutationFn: (isAutoScrape: boolean) =>
      updateLibrary(library.id, { is_auto_scrape: isAutoScrape }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      notifications.show({
        color: "green",
        message: saved.is_auto_scrape ? "已打开扫描后自动刮削" : "已关闭扫描后自动刮削",
      });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  const seriesLibrary = library.data_type_id === "tv" || library.data_type_id === "anime";
  const missing = useMutation({
    mutationFn: (show: boolean) => updateLibrary(library.id, { show_missing_episodes: show }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      notifications.show({
        color: "green",
        message: saved.show_missing_episodes === false ? "已隐藏缺少的集" : "已显示缺少的集",
      });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

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
    mutationFn: (kind: ActionKind) => {
      if (kind === "organize") {
        return organizeLibrary(library.id);
      }
      if (kind === "scrape" || kind === "rescrape") {
        return scrapeLibrary(library.id, kind === "rescrape");
      }
      return arrangeLibrary(library.id);
    },
    onMutate: (kind) => setRunning(kind),
    onSuccess: ({ task_id }, kind) => {
      if (kind === "arrange") {
        setPreviewing(false);
      }
      if (kind === "rescrape") {
        setConfirmingRescrape(false);
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
  const samplePosters = library.sample_posters ?? [];
  const scrapedCount = library.scraped_count ?? 0;
  const issueCount = library.issue_count ?? 0;
  const unscrapedCount = Math.max(
    0,
    library.item_count - scrapedCount - (library.manual_count ?? 0),
  );
  const needAttention = issueCount + unscrapedCount;
  const automation = automationSummary(library);
  const hasPosters = samplePosters.length > 0;

  return (
    <article className={classes.card}>
      <Link
        to={`/libraries/${library.id}`}
        className={[classes.strip, hasPosters ? "" : classes.stripEmpty].filter(Boolean).join(" ")}
        aria-label={`进入媒体库「${library.name}」`}
      >
        {samplePosters.map((posterUrl, index) => (
          <img
            key={`${posterUrl}-${index}`}
            src={posterUrl}
            alt=""
            className={classes.stripImg}
            loading="lazy"
          />
        ))}
        <span className={classes.kindBadge}>
          <DataTypeIcon dataTypeId={library.data_type_id} size={13} />
          {library.data_type_name}
        </span>
      </Link>

      <div className={classes.body}>
        <h2 className={classes.title}>
          <Link to={`/libraries/${library.id}`} className={classes.titleLink}>
            {library.name}
          </Link>
        </h2>

        <div className={classes.nums}>
          <span>
            <b className="mo-mono">{library.item_count}</b> {itemUnit}
          </span>
          <span>
            <b className="mo-mono">{library.file_count}</b> {fileUnit}
          </span>
          <span>
            <b className="mo-mono">{scrapedCount}</b> 已刮削
          </span>
          {needAttention > 0 && (
            <span className={classes.warnStat}>
              <b className="mo-mono">{needAttention}</b> 需处理
            </span>
          )}
        </div>

        <div className={classes.paths}>
          {library.paths.map((path) => (
            <code key={path} className={`${classes.pathItem} mo-mono`} title={path}>
              <IconFolder size={13} />
              <span>{path}</span>
            </code>
          ))}
        </div>

        <div className={classes.footer}>
          <div className={classes.autoGroup}>
            <Switch
              size="sm"
              label="扫描后自动刮削"
              checked={library.is_auto_scrape}
              disabled={toggle.isPending}
              onChange={(event) => toggle.mutate(event.currentTarget.checked)}
              styles={{
                label: {
                  fontSize: 12.5,
                  color: "var(--mo-dim)",
                  paddingLeft: 8,
                  cursor: "pointer",
                },
              }}
            />
            {seriesLibrary && (
              <Switch
                size="sm"
                label="显示缺少的集"
                checked={library.show_missing_episodes !== false}
                disabled={missing.isPending}
                onChange={(event) => missing.mutate(event.currentTarget.checked)}
                styles={{
                  label: {
                    fontSize: 12.5,
                    color: "var(--mo-dim)",
                    paddingLeft: 8,
                    cursor: "pointer",
                  },
                }}
              />
            )}
            <span
              className={[
                classes.autoMode,
                automation.tone === "ok" ? classes.autoOk : "",
                automation.tone === "bad" ? classes.autoBad : "",
              ]
                .filter(Boolean)
                .join(" ")}
              title={automation.title}
              aria-label="扫描方式"
            >
              · {automation.text}
            </span>
          </div>

          <div className={classes.actions}>
            <Button
              size="xs"
              variant="default"
              leftSection={<IconScan size={13} />}
              loading={running === "organize"}
              disabled={running !== null}
              onClick={() => start.mutate("organize")}
            >
              扫描
            </Button>
            <Button
              size="xs"
              variant="default"
              leftSection={<IconScrape size={13} />}
              loading={running === "scrape"}
              disabled={running !== null}
              onClick={() => start.mutate("scrape")}
            >
              刮削未刮削的
            </Button>
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <Button
                  size="xs"
                  variant="default"
                  className={classes.moreBtn}
                  aria-label="更多操作"
                  loading={running === "arrange" || running === "rescrape"}
                >
                  <IconMore size={14} />
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                {canArrange && (
                  <Menu.Item
                    leftSection={<IconOrganize size={14} />}
                    disabled={running !== null}
                    onClick={() => {
                      start.reset();
                      setPreviewing(true);
                    }}
                  >
                    整理整库
                  </Menu.Item>
                )}
                <Menu.Item leftSection={<IconEdit size={14} />} onClick={onEdit}>
                  编辑
                </Menu.Item>
                <Menu.Item
                  leftSection={<IconFolder size={14} />}
                  disabled={running !== null}
                  onClick={() => setCleaning(true)}
                >
                  清理空文件夹
                </Menu.Item>
                <Menu.Item
                  leftSection={<IconRefresh size={14} />}
                  disabled={running !== null}
                  onClick={() => setConfirmingRescrape(true)}
                >
                  重新刮削全部
                </Menu.Item>
                <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={onDelete}>
                  删除
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </div>
        </div>
      </div>

      <ArrangePreviewModal
        opened={previewing}
        onClose={() => setPreviewing(false)}
        plan={
          plan === undefined
            ? undefined
            : {
                rename: plan.rename,
                write: plan.write,
                scope: `整理 ${String(plan.count)} ${itemUnit}（刮削过的和手动条目）`,
                empty:
                  plan.count === 0
                    ? "这个库还没有能整理的条目：先刮削，或在「未识别」里手动建条目。"
                    : null,
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

      <EmptyFolderModal
        libraryId={library.id}
        opened={cleaning}
        onClose={() => setCleaning(false)}
      />

      <Modal
        opened={confirmingRescrape}
        onClose={() => setConfirmingRescrape(false)}
        title="重新刮削全部"
      >
        <Stack>
          <Text>
            「{library.name}」里已经认出的条目会全部重新刮削：不用之前存下的来源结果，
            重新向各个来源请求。锁定的字段（包括手动改过的）不变，没认出的不在其中。
            条目多时要排很多任务。
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirmingRescrape(false)}>
              取消
            </Button>
            <Button
              loading={running === "rescrape"}
              onClick={() => start.mutate("rescrape")}
            >
              开始重新刮削
            </Button>
          </Group>
        </Stack>
      </Modal>
    </article>
  );
}
