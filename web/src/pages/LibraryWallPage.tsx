import {
  Alert,
  Button,
  Loader,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import {
  type SortOrder,
  type TaskDetail,
  type WallSort,
  arrangeLibrary,
  fetchLibrary,
  fetchLibraryArrangePreview,
  fetchLibraryGenres,
  fetchWall,
  organizeLibrary,
  scrapeLibrary,
} from "../api";
import { ArrangePreviewModal } from "../components/ArrangePreviewModal";
import { automationSummary } from "../components/libraries/automation";
import { PosterCard } from "../components/PosterCard";
import { FilterChip, PageHeader, Pill } from "../components/ui";
import {
  ShelfPicker,
  TagShelves,
  type WallView,
  readShelfTags,
  readView,
  saveShelfTags,
  saveView,
} from "../components/wall/TagShelves";
import { fileUnit, hasShelves, isPluginType, itemUnit, useDataTypes } from "../dataTypes";
import {
  IconClose,
  IconOrganize,
  IconScan,
  IconScrape,
  IconSearch,
  IconSort,
} from "../icons";
import { useTaskWatcher } from "../useTaskWatcher";
import styles from "./LibraryWallPage.module.css";

const SORTS = [
  { value: "title:asc", label: "片名" },
  { value: "release_date:desc", label: "上映日期（新到旧）" },
  { value: "release_date:asc", label: "上映日期（旧到新）" },
  { value: "created_at:desc", label: "最近加入" },
  { value: "updated_at:desc", label: "最近更新" },
];
const DEFAULT_SORT = "title:asc";

const sortStorageKey = (libraryId: number) => `mortis-wall-sort-${libraryId}`;

function readSort(libraryId: number): string {
  const saved = window.localStorage.getItem(sortStorageKey(libraryId));
  return SORTS.some((option) => option.value === saved) ? (saved as string) : DEFAULT_SORT;
}

type ActionKind = "organize" | "scrape" | "arrange";
type StatusFilter = "all" | "scraped" | "unscraped" | "issue";

const START_MESSAGES: Record<ActionKind, string> = {
  organize: "已开始扫描",
  scrape: "已开始刮削还没刮削过的条目",
  arrange: "已开始整理整库",
};

const DONE_LABELS: Record<string, string> = {
  organize: "扫描",
  scrape_library: "刮削",
  arrange_library: "整理",
};

function taskSummary(task: TaskDetail, pluginType: boolean): string {
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

export function LibraryWallPage() {
  const id = Number(useParams().libraryId);
  const queryClient = useQueryClient();
  const library = useQuery({ queryKey: ["library", id], queryFn: () => fetchLibrary(id) });
  const dataTypes = useDataTypes();

  const [search, setSearch] = useState("");
  const [query] = useDebouncedValue(search, 300);
  const [savedSort, setSavedSort] = useState(() => ({ id, value: readSort(id) }));
  const sortKey = savedSort.id === id ? savedSort.value : readSort(id);
  const setSortKey = (value: string) => {
    window.localStorage.setItem(sortStorageKey(id), value);
    setSavedSort({ id, value });
  };
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sort, order] = sortKey.split(":") as [WallSort, SortOrder];
  const searchInputRef = useRef<HTMLInputElement>(null);

  const libData = library.data;
  const shelvesOn = libData !== undefined && hasShelves(libData.data_type_id);
  const [savedView, setSavedView] = useState(() => ({ id, value: readView(id) }));
  const storedView = savedView.id === id ? savedView.value : readView(id);
  const view: WallView = shelvesOn ? storedView : "wall";
  const setView = (value: WallView) => {
    saveView(id, value);
    setSavedView({ id, value });
  };
  const [savedShelves, setSavedShelves] = useState(() => ({ id, value: readShelfTags(id) }));
  const shelfIds = savedShelves.id === id ? savedShelves.value : readShelfTags(id);
  const setShelfIds = (value: number[]) => {
    saveShelfTags(id, value);
    setSavedShelves({ id, value });
  };
  // 分类视图里点「查看全部」后，海报墙只看这个类型。
  const [tagFilter, setTagFilter] = useState<{ id: number; name: string } | null>(null);
  const genres = useQuery({
    queryKey: ["genres", id],
    queryFn: () => fetchLibraryGenres(id),
    enabled: shelvesOn,
  });
  // 存下的勾选里可能有库里已经没有的类型，取数前去掉。
  const shownTags = genres.data
    ? shelfIds.filter((tagId) => genres.data.some((tag) => tag.id === tagId))
    : null;

  const wall = useQuery({
    queryKey: ["wall", id, query, sort, order, tagFilter?.id ?? null],
    queryFn: () => fetchWall(id, query, sort, order, tagFilter === null ? [] : [tagFilter.id]),
    enabled: libData !== undefined && view === "wall",
  });

  const [running, setRunning] = useState<ActionKind | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const canArrange =
    libData !== undefined && !isPluginType(dataTypes.data, libData.data_type_id);

  const watcher = useTaskWatcher((task) => {
    setRunning(null);
    void queryClient.invalidateQueries({ queryKey: ["libraries"] });
    void queryClient.invalidateQueries({ queryKey: ["library", id] });
    void queryClient.invalidateQueries({ queryKey: ["wall", id] });
    void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
    void queryClient.invalidateQueries({ queryKey: ["library-arrange-preview", id] });
    const failed = task.tree_status.failed ?? 0;
    const libName = libData?.name ?? "媒体库";
    notifications.show({
      color: task.status === "succeeded" && failed === 0 ? "green" : "red",
      title: `「${libName}」${DONE_LABELS[task.task_type] ?? task.type_label}结束`,
      message:
        failed > 0
          ? `${taskSummary(task, !canArrange)}；有 ${String(failed)} 部失败，详情看任务页`
          : taskSummary(task, !canArrange),
    });
  });

  const start = useMutation({
    mutationFn: (kind: ActionKind) => {
      if (kind === "organize") {
        return organizeLibrary(id);
      }
      if (kind === "scrape") {
        return scrapeLibrary(id);
      }
      return arrangeLibrary(id);
    },
    onMutate: (kind) => setRunning(kind),
    onSuccess: ({ task_id }, kind) => {
      if (kind === "arrange") {
        setPreviewing(false);
      }
      watcher.watch(task_id);
      notifications.show({
        message:
          kind === "organize" && libData?.is_auto_scrape
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
    queryKey: ["library-arrange-preview", id],
    queryFn: () => fetchLibraryArrangePreview(id),
    enabled: previewing,
    retry: false,
  });

  // 切换媒体库时重置状态筛选，避免残留的筛选条件让新库看似为空。
  useEffect(() => {
    setStatusFilter("all");
    setTagFilter(null);
  }, [id]);

  // Ctrl K 聚焦搜索框。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const items = wall.data ?? [];
  const counts = useMemo(() => {
    let scraped = 0;
    let unscraped = 0;
    let issue = 0;
    for (const item of items) {
      if (item.is_scraped) {
        scraped += 1;
      } else {
        unscraped += 1;
      }
      if (typeof item.issue === "string" && item.issue !== "") {
        issue += 1;
      }
    }
    return { all: items.length, scraped, unscraped, issue };
  }, [items]);

  const filteredItems = useMemo(() => {
    switch (statusFilter) {
      case "scraped":
        return items.filter((item) => item.is_scraped);
      case "unscraped":
        return items.filter((item) => !item.is_scraped);
      case "issue":
        return items.filter((item) => typeof item.issue === "string" && item.issue !== "");
      default:
        return items;
    }
  }, [items, statusFilter]);

  if (library.isError) {
    return (
      <Alert color="red" title="读不到媒体库">
        {library.error.message}
      </Alert>
    );
  }

  const unit = libData !== undefined ? itemUnit(dataTypes.data, libData.data_type_id) : "部";
  const filesUnit = libData !== undefined ? fileUnit(dataTypes.data, libData.data_type_id) : "个视频";
  const scrapedTotal =
    typeof libData?.scraped_count === "number"
      ? libData.scraped_count
      : query === "" && wall.isSuccess
        ? counts.scraped
        : null;
  const issueTotal =
    typeof libData?.issue_count === "number"
      ? libData.issue_count
      : query === "" && wall.isSuccess
        ? counts.issue
        : 0;
  const pendingCount =
    libData !== undefined && scrapedTotal !== null
      ? Math.max(0, libData.item_count - scrapedTotal)
      : 0;
  const plan = preview.data;

  return (
    <Stack gap={0}>
      <PageHeader
        title={libData?.name ?? "媒体库"}
        pills={
          libData !== undefined ? (
            <>
              <Pill>
                <span className="mo-mono">{libData.item_count}</span> {unit}
              </Pill>
              {scrapedTotal !== null && (
                <Pill tone="ok">
                  已刮削 <span className="mo-mono">{scrapedTotal}</span>
                </Pill>
              )}
              {pendingCount > 0 && (
                <Pill tone="warn">
                  待处理 <span className="mo-mono">{pendingCount}</span>
                </Pill>
              )}
              {issueTotal > 0 && (
                <Pill tone="bad">
                  异常 <span className="mo-mono">{issueTotal}</span>
                </Pill>
              )}
            </>
          ) : (
            wall.isSuccess && <Pill>{wall.data.length} 部</Pill>
          )
        }
        description={
          libData !== undefined ? (
            <span className={styles.headerMeta}>
              {libData.paths.map((path) => (
                <code key={path} className={`${styles.pathTag} mo-mono`}>
                  {path}
                </code>
              ))}
              <span className="mo-mono">
                {libData.file_count} {filesUnit}
              </span>
              <span aria-hidden="true">·</span>
              <span title={automationSummary(libData).title}>
                {automationSummary(libData).text}
              </span>
              <span aria-hidden="true">·</span>
              <span>{libData.is_auto_scrape ? "扫描后自动刮削" : "只扫描不自动刮削"}</span>
            </span>
          ) : undefined
        }
        actions={
          libData !== undefined && (
            <>
              <Button
                variant="filled"
                leftSection={<IconScan size={15} />}
                loading={running === "organize"}
                disabled={running !== null}
                onClick={() => start.mutate("organize")}
              >
                扫描
              </Button>
              <Button
                variant="light"
                leftSection={<IconScrape size={15} />}
                loading={running === "scrape"}
                disabled={running !== null}
                onClick={() => start.mutate("scrape")}
              >
                刮削未刮削的
                <span className={`${styles.btnCount} mo-mono`}>{pendingCount}</span>
              </Button>
              {canArrange && (
                <Button
                  variant="default"
                  leftSection={<IconOrganize size={15} />}
                  loading={running === "arrange"}
                  disabled={running !== null}
                  onClick={() => {
                    start.reset();
                    setPreviewing(true);
                  }}
                >
                  整理整库
                </Button>
              )}
            </>
          )
        }
      />

      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          {/* 分类视图不搜索，也不按刮削状态筛选。 */}
          {view === "wall" && (
            <>
              <TextInput
                ref={searchInputRef}
                className={styles.searchWrap}
                classNames={{ input: styles.searchInput }}
                size="sm"
                placeholder="搜索片名或原名..."
                aria-label="搜索片名"
                leftSection={<IconSearch size={14} />}
                rightSection={
                  search === "" ? (
                    <kbd className={styles.kbd}>Ctrl K</kbd>
                  ) : (
                    <button
                      type="button"
                      className={styles.clearBtn}
                      aria-label="清除搜索"
                      onClick={() => setSearch("")}
                    >
                      <IconClose size={13} />
                    </button>
                  )
                }
                rightSectionWidth={search === "" ? 58 : 30}
                value={search}
                onChange={(event) => setSearch(event.currentTarget.value)}
              />

              <div className={styles.chips} role="group" aria-label="状态筛选">
                <FilterChip
                  active={statusFilter === "all"}
                  count={counts.all}
                  onClick={() => setStatusFilter("all")}
                >
                  全部
                </FilterChip>
                <FilterChip
                  active={statusFilter === "scraped"}
                  count={counts.scraped}
                  onClick={() => setStatusFilter("scraped")}
                >
                  已刮削
                </FilterChip>
                <FilterChip
                  active={statusFilter === "unscraped"}
                  count={counts.unscraped}
                  onClick={() => setStatusFilter("unscraped")}
                >
                  未刮削
                </FilterChip>
                <FilterChip
                  active={statusFilter === "issue"}
                  count={counts.issue}
                  onClick={() => setStatusFilter("issue")}
                >
                  有问题
                </FilterChip>
                {tagFilter !== null && (
                  <FilterChip active onClick={() => setTagFilter(null)}>
                    按类型：{tagFilter.name}
                    <IconClose size={12} />
                  </FilterChip>
                )}
              </div>
            </>
          )}
        </div>

        <div className={styles.toolbarRight}>
          {shelvesOn && (
            <SegmentedControl
              size="sm"
              value={view}
              onChange={(value) => setView(value as WallView)}
              data={[
                { value: "wall", label: "海报墙" },
                { value: "shelves", label: "分类" },
              ]}
              aria-label="显示方式"
            />
          )}
          {view === "shelves" && (
            <ShelfPicker tags={genres.data ?? []} selected={shownTags ?? []} onChange={setShelfIds} />
          )}
          <Select
            data={SORTS}
            value={sortKey}
            onChange={(value) => setSortKey(value ?? DEFAULT_SORT)}
            allowDeselect={false}
            leftSection={<IconSort size={14} />}
            classNames={{ input: styles.sortInput }}
            size="sm"
            w={195}
            aria-label="排序"
          />
        </div>
      </div>

      {view === "shelves" && genres.isPending && <Loader size="sm" />}

      {view === "shelves" && genres.isError && (
        <Alert color="red" title="读不到类型">
          {genres.error.message}
        </Alert>
      )}

      {view === "shelves" && shownTags !== null && (
        <TagShelves
          libraryId={id}
          tagIds={shownTags}
          sort={sort}
          order={order}
          unit={unit}
          onViewAll={(tag) => {
            setStatusFilter("all");
            setSearch("");
            setTagFilter(tag);
            setView("wall");
          }}
        />
      )}

      {view === "wall" && wall.isPending && <Loader size="sm" />}

      {view === "wall" && wall.isError && (
        <Alert color="red" title="读不到条目">
          {wall.error.message}
        </Alert>
      )}

      {view === "wall" && wall.isSuccess && wall.data.length === 0 && (
        <div className={styles.emptyBox}>
          <Text c="var(--mo-dim)" size="sm">
            {query === "" && tagFilter === null
              ? `库里还没有条目。点「扫描」找出目录里的内容。`
              : "没有找到匹配的条目。"}
          </Text>
        </div>
      )}

      {view === "wall" && wall.isSuccess && wall.data.length > 0 && filteredItems.length === 0 && (
        <div className={styles.emptyBox}>
          <Text c="var(--mo-dim)" size="sm">
            当前筛选下没有条目。
          </Text>
          <Button size="xs" variant="default" onClick={() => setStatusFilter("all")}>
            查看全部
          </Button>
        </div>
      )}

      {view === "wall" && wall.isSuccess && filteredItems.length > 0 && (
        <section className={styles.wallGrid} aria-label="海报墙">
          {filteredItems.map((item) => (
            <div key={item.id} className={styles.wallItem}>
              <PosterCard item={item} />
            </div>
          ))}
        </section>
      )}

      {libData !== undefined && canArrange && (
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
      )}
    </Stack>
  );
}
