import {
  ActionIcon,
  Alert,
  Avatar,
  Button,
  Loader,
  Menu,
  Paper,
  SimpleGrid,
  Tabs,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Link, useParams } from "react-router";
import {
  type FieldOut,
  type ImageValue,
  type ItemDetail,
  type MovieHit,
  type PersonValue,
  type RatingValue,
  addItemSource,
  arrangeItem,
  fetchItem,
  fetchItemArrangePreview,
  fetchSources,
  rematchItem,
  rematchItemToSource,
  scrapeItem,
  updateItemFields,
  updateItemLocks,
} from "../api";
import { ArrangePreviewModal } from "../components/ArrangePreviewModal";
import { DeleteItemModal } from "../components/DeleteItemModal";
import { FieldActions } from "../components/detail/FieldActions";
import { FieldEditModal } from "../components/FieldEditModal";
import { FieldValue } from "../components/FieldValue";
import { usePlayItem } from "../components/PlayItem";
import { PosterCard } from "../components/PosterCard";
import { SeasonRails } from "../components/episodes/SeasonRail";
import { SourceSearchModal } from "../components/SourceSearchModal";
import { TmdbSearchModal } from "../components/TmdbSearchModal";
import { Pill, ratingText, sourceLabel } from "../components/ui";
import { isPluginType, isSeriesType, useDataTypes } from "../dataTypes";
import { formatSize, formatTime } from "../format";
import {
  IconBack,
  IconExternal,
  IconFile,
  IconFolder,
  IconGame,
  IconImage,
  IconInfo,
  IconLink,
  IconMore,
  IconOrganize,
  IconPlay,
  IconRefresh,
  IconScrape,
  IconStar,
  IconSubtitle,
  IconTrash,
  IconVideo,
} from "../icons";
import { useTaskWatcher } from "../useTaskWatcher";
import classes from "./ItemDetailPage.module.css";

function externalLink(
  provider: string,
  id: string,
  kind: string,
  series: boolean,
): string | null {
  if (provider === "tmdb") {
    const path = kind === "collection" ? "collection" : series ? "tv" : "movie";
    return `https://www.themoviedb.org/${path}/${id}`;
  }
  if (provider === "imdb") {
    return `https://www.imdb.com/title/${id}/`;
  }
  if (provider === "bangumi") {
    return `https://bgm.tv/subject/${id}`;
  }
  if (provider === "vndb") {
    return `https://vndb.org/${id}`;
  }
  if (provider === "ymgal") {
    return `https://www.ymgal.games/ga${id}`;
  }
  if (provider === "cngal") {
    return `https://www.cngal.org/entries/index/${id}`;
  }
  return null;
}

// 只列视频、字幕和游戏文件夹；写出的 NFO、图片、演员头像不在这里显示。
const FILE_KIND_LABELS: Record<string, string> = {
  video: "视频",
  subtitle: "字幕",
  game: "游戏文件夹",
};
const FILE_KIND_ICONS: Record<string, ReactNode> = {
  video: <IconVideo size={16} />,
  subtitle: <IconSubtitle size={16} />,
  game: <IconFolder size={16} />,
};

// 概览里放的字段；资料栏不再重复列出。
const OVERVIEW_KEYS = new Set(["overview"]);
const MAIN_CAST_KEYS = ["actors", "cast"];
const CAST_SHOWN = 12;

const TAGS_SHOWN = 8;

function TagsValue({ tags }: { tags: string[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? tags : tags.slice(0, TAGS_SHOWN);
  return (
    <div className={classes.tags}>
      {shown.map((tag) => (
        <Pill key={tag}>{tag}</Pill>
      ))}
      {tags.length > TAGS_SHOWN && (
        <button
          type="button"
          className={classes.tagsMore}
          onClick={() => setAll(!all)}
        >
          {all ? "收起" : `等 ${tags.length} 个`}
        </button>
      )}
    </div>
  );
}

function isEmpty(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function firstImage(field: FieldOut | undefined): string | null {
  if (field === undefined || !Array.isArray(field.value)) {
    return null;
  }
  return (
    (field.value as ImageValue[]).find((image) => image.url !== null)?.url ??
    null
  );
}

function textValue(fields: Map<string, FieldOut>, key: string): string | null {
  const value = fields.get(key)?.value;
  return typeof value === "string" && value !== "" ? value : null;
}

export function ItemDetailPage() {
  const id = Number(useParams().itemId);
  const queryClient = useQueryClient();
  const item = useQuery({
    queryKey: ["item", id],
    queryFn: () => fetchItem(id),
  });
  const dataTypes = useDataTypes();
  const [editing, setEditing] = useState<FieldOut | null>(null);
  const [rematching, setRematching] = useState(false);
  const [supplementing, setSupplementing] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [arranging, setArranging] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tab, setTab] = useState<string | null>("overview");
  const setDetail = (detail: ItemDetail) => {
    queryClient.setQueryData(["item", id], detail);
    void queryClient.invalidateQueries({
      queryKey: ["wall", detail.library_id],
    });
  };
  const watcher = useTaskWatcher((task) => {
    setArranging(false);
    void queryClient.invalidateQueries({ queryKey: ["item", id] });
    void queryClient.invalidateQueries({
      queryKey: ["item-arrange-preview", id],
    });
    void queryClient.invalidateQueries({ queryKey: ["wall"] });
    notifications.show(
      task.status === "succeeded"
        ? { color: "green", message: `${task.type_label}完成` }
        : {
            color: "red",
            title: `${task.type_label}失败`,
            message: task.error ?? "任务没有完成",
          },
    );
  });
  const player = usePlayItem();
  const scrape = useMutation({
    mutationFn: () => scrapeItem(id, true),
    onSuccess: ({ task_id }) => watcher.watch(task_id),
    onError: (error) =>
      notifications.show({ color: "red", message: error.message }),
  });
  const preview = useQuery({
    queryKey: ["item-arrange-preview", id],
    queryFn: () => fetchItemArrangePreview(id),
    enabled: previewing,
    retry: false,
  });
  const arrange = useMutation({
    mutationFn: () => arrangeItem(id),
    onMutate: () => setArranging(true),
    onSuccess: ({ task_id }) => {
      setPreviewing(false);
      watcher.watch(task_id);
      notifications.show({ message: "已开始整理" });
    },
    onError: () => setArranging(false),
  });
  const rematch = useMutation({
    mutationFn: (
      target: { tmdbId: number } | { source: string; externalId: string },
    ) =>
      "tmdbId" in target
        ? rematchItem(id, target.tmdbId)
        : rematchItemToSource(id, target.source, target.externalId),
    onSuccess: ({ task_id }) => {
      setRematching(false);
      watcher.watch(task_id);
      notifications.show({ message: "已换成新的条目，正在刮削" });
    },
  });
  const supplement = useMutation({
    mutationFn: ({
      source,
      externalId,
    }: {
      source: string;
      externalId: string;
    }) => addItemSource(id, source, externalId),
    onSuccess: ({ task_id }) => {
      setSupplementing(false);
      watcher.watch(task_id);
      notifications.show({ message: "已补充来源，正在刮削" });
    },
  });
  const dataTypeId = item.data?.data_type_id ?? "";
  const pluginType = isPluginType(dataTypes.data, dataTypeId);
  const sources = useQuery({
    queryKey: ["sources", dataTypeId],
    queryFn: () => fetchSources(dataTypeId),
    enabled: pluginType,
  });
  const edit = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) =>
      updateItemFields(id, { [key]: value }),
    onSuccess: (detail) => {
      setDetail(detail);
      notifications.show({
        color: "green",
        message: `已保存「${editing?.label ?? ""}」并锁定`,
      });
      setEditing(null);
    },
  });
  const locks = useMutation({
    mutationFn: (keys: string[]) => updateItemLocks(id, keys),
    onSuccess: setDetail,
    onError: (error) =>
      notifications.show({ color: "red", message: error.message }),
  });

  if (item.isPending) {
    return <Loader size="sm" />;
  }
  if (item.isError) {
    return (
      <Alert color="red" title="读不到条目">
        {item.error.message}
      </Alert>
    );
  }
  const detail = item.data;
  const series = isSeriesType(dataTypes.data, detail.data_type_id);
  const allFields = detail.sections.flatMap((section) => section.fields);
  const byKey = new Map(allFields.map((field) => [field.key, field]));
  const lockedKeys = allFields
    .filter((field) => field.locked)
    .map((field) => field.key);
  const originalTitle = textValue(byKey, "original_title");
  const toggleLock = (field: FieldOut) =>
    locks.mutate(
      field.locked
        ? lockedKeys.filter((key) => key !== field.key)
        : [...lockedKeys, field.key],
    );
  const startEdit = (field: FieldOut) => {
    edit.reset();
    setEditing(field);
  };
  const sourceName = (source: string) =>
    sources.data?.find((entry) => entry.id === source)?.name ??
    sourceLabel(source);
  const actions = (field: FieldOut) => (
    <FieldActions
      field={field}
      sourceName={sourceName}
      lockBusy={locks.isPending}
      onEdit={startEdit}
      onToggleLock={toggleLock}
    />
  );

  const busy = watcher.watching || scrape.isPending;
  const hasVideo =
    detail.files.some((file) => file.file_kind === "video") ||
    detail.seasons.some((season) => season.file_count > 0);
  const sourceFiles = detail.files.filter(
    (file) => file.file_kind in FILE_KIND_LABELS,
  );
  // 刮削、重新匹配和整理只对剧或电影（work）显示，季和集不显示。
  const isWork = detail.kind === "work";
  const canArrange = isWork && hasVideo && detail.scraped_at !== null;
  // 剧、季和系列不能直接播放；电影、集和单条目有视频文件才显示。
  const canPlay =
    (detail.kind === "episode" || (isWork && !series)) &&
    detail.files.some((file) => file.file_kind === "video");
  // 本地文件是游戏文件夹的条目（Galgame）按钮叫「启动」，启动的是文件夹里选定的程序。
  const canLaunch = detail.files.some((file) => file.file_kind === "game");
  // 只有单部作品能删；剧、季、集和系列后端也不让删。
  const canDelete = dataTypes.data !== undefined && isWork && !series;
  const landscape = detail.kind === "episode";

  const peopleFields = allFields.filter(
    (field) => field.value_type === "people",
  );
  const imageFields = allFields.filter(
    (field) => field.value_type === "images",
  );
  const overviewFields = allFields.filter((field) =>
    OVERVIEW_KEYS.has(field.key),
  );
  const panelSections = detail.sections
    .map((section) => ({
      name: section.name,
      fields: section.fields.filter(
        (field) =>
          field.value_type !== "people" &&
          field.value_type !== "images" &&
          !OVERVIEW_KEYS.has(field.key),
      ),
    }))
    .filter((section) => section.fields.length > 0);
  const peopleCount = peopleFields.reduce(
    (sum, field) => sum + (Array.isArray(field.value) ? field.value.length : 0),
    0,
  );
  const imagesFilled = imageFields.filter(
    (field) => firstImage(field) !== null,
  ).length;
  const mainCast =
    MAIN_CAST_KEYS.map((key) => byKey.get(key)).find(
      (field) => field !== undefined,
    ) ??
    [...peopleFields].sort(
      (a, b) =>
        (Array.isArray(b.value) ? b.value.length : 0) -
        (Array.isArray(a.value) ? a.value.length : 0),
    )[0];
  const crewFields = peopleFields.filter(
    (field) => field !== mainCast && !isEmpty(field.value),
  );

  const logo = firstImage(byKey.get("clearlogo"));
  const tagline = textValue(byKey, "tagline");
  const metaPills: ReactNode[] = [];
  const rating = allFields.find(
    (field) => field.value_type === "rating" && !isEmpty(field.value),
  );
  if (rating !== undefined) {
    const best = (rating.value as RatingValue[])[0];
    metaPills.push(
      <Pill key="rating" tone="accent">
        <IconStar size={12} /> {ratingText(best.rating, best.max_rating)}{" "}
        {sourceLabel(best.source)}
      </Pill>,
    );
  }
  const released = textValue(byKey, "release_date");
  if (released !== null) {
    metaPills.push(<Pill key="date">{released}</Pill>);
  } else if (detail.year !== null) {
    metaPills.push(<Pill key="year">{detail.year}</Pill>);
  }
  const runtime = byKey.get("runtime")?.value;
  if (typeof runtime === "number" && runtime > 0) {
    metaPills.push(<Pill key="runtime">{runtime} 分钟</Pill>);
  }
  const certification = textValue(byKey, "certification");
  if (certification !== null) {
    metaPills.push(<Pill key="cert">{certification}</Pill>);
  }
  const genres = byKey.get("genres")?.value;
  if (Array.isArray(genres)) {
    for (const genre of genres.slice(0, 4)) {
      metaPills.push(<Pill key={`genre-${genre}`}>{String(genre)}</Pill>);
    }
  }
  if (detail.collection !== null) {
    metaPills.push(
      <Link
        key="collection"
        to={`/items/${detail.collection.id}`}
        className={classes.ext}
      >
        系列 <b>{detail.collection.title}</b>
      </Link>,
    );
  }

  const hero = (
    <div className={classes.hero}>
      {detail.fanart_url !== null && (
        <div
          className={classes.heroBg}
          style={{ backgroundImage: `url("${detail.fanart_url}")` }}
        />
      )}
      <span className={classes.scrapedAt}>
        {detail.scraped_at !== null
          ? `上次刮削 ${formatTime(detail.scraped_at)}`
          : "还没刮削"}
      </span>
      <div className={classes.heroInner}>
        <div
          className={`${classes.poster} ${landscape ? classes.landscape : ""}`}
        >
          {detail.poster_url !== null ? (
            <img src={detail.poster_url} alt={detail.title} />
          ) : (
            <div
              className={classes.posterBlank}
              style={landscape ? { aspectRatio: "16 / 9" } : undefined}
            >
              <IconImage size={32} />
            </div>
          )}
        </div>
        <div className={classes.heroText}>
          {detail.show !== null && (
            <Link
              to={`/items/${detail.show.id}`}
              className={classes.crumb}
              style={{ marginBottom: 8 }}
            >
              {detail.show.title}
              {detail.position !== null ? ` · ${detail.position}` : ""}
            </Link>
          )}
          {logo !== null ? (
            <>
              <img className={classes.logo} src={logo} alt={detail.title} />
              <h1 className="mo-sr-only">{detail.title}</h1>
            </>
          ) : (
            <h1 className={classes.title}>{detail.title}</h1>
          )}
          {(detail.subtitle !== null ||
            (logo !== null && detail.year !== null)) && (
            <div className={classes.subtitle}>
              {logo !== null
                ? `${detail.title}${detail.year !== null ? `（${detail.year}）` : ""}`
                : detail.subtitle}
            </div>
          )}
          {metaPills.length > 0 && (
            <div className={classes.meta}>{metaPills}</div>
          )}
          {tagline !== null && (
            <div className={classes.tagline}>「{tagline}」</div>
          )}
          {(isWork || canPlay || canLaunch) && (
            <div className={classes.actions}>
              {canLaunch ? (
                <>
                  <Button
                    leftSection={<IconPlay size={16} />}
                    loading={player.playingId === detail.id}
                    disabled={player.rechoosingId === detail.id}
                    onClick={() =>
                      player.play(detail.id, detail.title, { game: true })
                    }
                  >
                    启动
                  </Button>
                  <Button
                    variant="default"
                    leftSection={<IconGame size={16} />}
                    loading={player.rechoosingId === detail.id}
                    disabled={player.playingId === detail.id}
                    onClick={() =>
                      player.play(detail.id, detail.title, {
                        game: true,
                        choose: true,
                      })
                    }
                  >
                    更换启动程序
                  </Button>
                </>
              ) : (
                canPlay && (
                  <Button
                    leftSection={<IconPlay size={16} />}
                    loading={player.playingId === detail.id}
                    onClick={() => player.play(detail.id, detail.title)}
                  >
                    播放
                  </Button>
                )
              )}
              {isWork && (
                <Button
                  variant={canPlay || canLaunch ? "default" : "filled"}
                  leftSection={<IconScrape size={16} />}
                  loading={busy && !arranging}
                  disabled={busy}
                  onClick={() => scrape.mutate()}
                >
                  刮削
                </Button>
              )}
              {isWork && (
                <Button
                  variant="default"
                  leftSection={<IconRefresh size={16} />}
                  disabled={busy}
                  onClick={() => {
                    rematch.reset();
                    setRematching(true);
                  }}
                >
                  重新匹配
                </Button>
              )}
              {isWork && pluginType && (
                <Button
                  variant="default"
                  leftSection={<IconLink size={16} />}
                  disabled={busy}
                  onClick={() => {
                    supplement.reset();
                    setSupplementing(true);
                  }}
                >
                  补充来源
                </Button>
              )}
              {canArrange && (
                <Button
                  variant="default"
                  leftSection={<IconOrganize size={16} />}
                  loading={arranging}
                  disabled={busy}
                  onClick={() => {
                    arrange.reset();
                    setPreviewing(true);
                  }}
                >
                  整理
                </Button>
              )}
              {canDelete && (
                <Menu position="bottom-end" withinPortal>
                  <Menu.Target>
                    <Button variant="default" aria-label="更多操作">
                      <IconMore size={16} />
                    </Button>
                  </Menu.Target>
                  <Menu.Dropdown>
                    <Menu.Item
                      color="red"
                      leftSection={<IconTrash size={14} />}
                      disabled={busy}
                      onClick={() => setDeleting(true)}
                    >
                      删除
                    </Menu.Item>
                  </Menu.Dropdown>
                </Menu>
              )}
            </div>
          )}
          {Object.keys(detail.external_ids).length > 0 && (
            <div className={classes.actions} style={{ marginTop: 14, gap: 6 }}>
              {Object.entries(detail.external_ids).map(([provider, value]) => {
                const href = externalLink(provider, value, detail.kind, series);
                const body = (
                  <>
                    <b>{sourceLabel(provider)}</b> {value}
                    {href !== null && <IconExternal size={11} />}
                  </>
                );
                return href !== null ? (
                  <a
                    key={provider}
                    className={classes.ext}
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {body}
                  </a>
                ) : (
                  <span key={provider} className={classes.ext}>
                    {body}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );

  const sectionHead = (
    title: string,
    extra?: ReactNode,
    more?: { label: string; tab: string },
  ) => (
    <div className={classes.sectionHead}>
      <h3>{title}</h3>
      {extra}
      {more !== undefined && (
        <button
          type="button"
          className={classes.more}
          onClick={() => setTab(more.tab)}
        >
          {more.label}
        </button>
      )}
    </div>
  );

  const personGrid = (people: PersonValue[]) => (
    <div className={classes.people}>
      {people.map((person, index) => (
        <div key={`${person.id}-${index}`} className={classes.person}>
          <Avatar
            src={person.photo_url}
            alt={person.name}
            size={72}
            radius={999}
            mx="auto"
          >
            {person.name.slice(0, 1)}
          </Avatar>
          <b title={person.name}>{person.name}</b>
          {(person.character ?? person.original_name) !== null && (
            <small title={person.character ?? person.original_name ?? ""}>
              {person.character ?? person.original_name}
            </small>
          )}
        </div>
      ))}
    </div>
  );

  const imageSlot = (field: FieldOut) => {
    const images = Array.isArray(field.value)
      ? (field.value as ImageValue[]).filter((image) => image.url !== null)
      : [];
    const url = images[0]?.url ?? null;
    const transparent = [
      "clearlogo",
      "clearart",
      "logo",
      "characterart",
    ].includes(field.key);
    return (
      <div
        key={field.key}
        className={`${classes.slot} ${classes.hoverRow} ${url === null ? classes.slotEmpty : ""}`}
      >
        {url !== null ? (
          <div
            className={`${classes.slotPic} ${transparent ? classes.checker : ""} ${
              ["fanart", "thumb", "landscape", "banner"].includes(field.key)
                ? classes.cover
                : ""
            }`}
          >
            <img src={url} alt={field.label} loading="lazy" />
          </div>
        ) : (
          <div className={classes.slotPic}>
            <IconImage size={22} />缺{field.label}
          </div>
        )}
        <div className={classes.slotCap}>
          <Text size="sm" fw={600} c="var(--mo-text)">
            {field.label}
          </Text>
          {images.length > 1 && (
            <span className="mo-mono">{images.length} 张</span>
          )}
          <span style={{ marginLeft: "auto" }}>{actions(field)}</span>
        </div>
      </div>
    );
  };

  const overviewTab = (
    <>
      {overviewFields.map((field) => (
        <div
          key={field.key}
          className={`${classes.section} ${classes.hoverRow}`}
        >
          {sectionHead(field.label, actions(field))}
          {isEmpty(field.value) ? (
            <div className={classes.empty}>还没有{field.label}</div>
          ) : (
            <div className={classes.overview}>{String(field.value)}</div>
          )}
        </div>
      ))}
      {detail.parts.length > 0 && (
        <div className={classes.section}>
          {sectionHead("系列里的电影", <small>{detail.parts.length} 部</small>)}
          <SimpleGrid cols={{ base: 2, sm: 4, lg: 5 }}>
            {detail.parts.map((part) => (
              <PosterCard key={part.id} item={part} />
            ))}
          </SimpleGrid>
        </div>
      )}
      {detail.seasons.length > 0 && (
        <div className={classes.section}>
          {sectionHead("季和集", <small>{detail.seasons.length} 季</small>)}
          <SeasonRails
            seasons={detail.seasons}
            renderEpisodeAction={(episode) =>
              episode.playable ? (
                <ActionIcon
                  size={44}
                  radius="xl"
                  aria-label={`播放第 ${episode.number ?? "?"} 集 ${episode.title}`}
                  loading={player.playingId === episode.id}
                  onClick={() =>
                    player.play(
                      episode.id,
                      `第 ${episode.number ?? "?"} 集 ${episode.title}`,
                    )
                  }
                >
                  <IconPlay size={20} />
                </ActionIcon>
              ) : null
            }
          />
        </div>
      )}
      {mainCast !== undefined && !isEmpty(mainCast.value) && (
        <div className={classes.section}>
          {sectionHead(
            mainCast.label,
            <small>{(mainCast.value as PersonValue[]).length} 人</small>,
            { label: "全部演职员", tab: "people" },
          )}
          {crewFields.length > 0 && (
            <div className={classes.crew}>
              {crewFields.map((field) => (
                <div key={field.key}>
                  <span>{field.label}</span>
                  <b>
                    {(field.value as PersonValue[])
                      .slice(0, 3)
                      .map((person) => person.name)
                      .join("、")}
                  </b>
                </div>
              ))}
            </div>
          )}
          {personGrid((mainCast.value as PersonValue[]).slice(0, CAST_SHOWN))}
        </div>
      )}
      {imagesFilled > 0 && (
        <div className={classes.section}>
          {sectionHead(
            "图片",
            <small>
              {imagesFilled} / {imageFields.length}
            </small>,
            {
              label: "全部图片",
              tab: "images",
            },
          )}
          <div className={classes.images}>
            {imageFields
              .filter((field) => firstImage(field) !== null)
              .slice(0, 4)
              .map(imageSlot)}
          </div>
        </div>
      )}
    </>
  );

  const peopleTab =
    peopleFields.length === 0 ? (
      <div className={classes.empty}>这个类型没有人员字段</div>
    ) : (
      peopleFields.map((field) => (
        <div
          key={field.key}
          className={`${classes.section} ${classes.hoverRow}`}
        >
          {sectionHead(
            field.label,
            <>
              <small>
                {Array.isArray(field.value) ? field.value.length : 0} 人
              </small>
              <span style={{ marginLeft: "auto" }}>{actions(field)}</span>
            </>,
          )}
          {isEmpty(field.value) ? (
            <div className={classes.empty}>没有{field.label}</div>
          ) : (
            personGrid(field.value as PersonValue[])
          )}
        </div>
      ))
    );

  const filesTab =
    sourceFiles.length === 0 ? (
      <div className={classes.empty}>
        {detail.seasons.length > 0
          ? "文件在各集里，点开一集查看"
          : "没有关联的文件"}
      </div>
    ) : (
      <Paper withBorder radius="lg" style={{ overflow: "hidden" }}>
        {sourceFiles.map((file) => (
          <div key={file.id} className={classes.file}>
            <span
              className={`${classes.fileIcon} ${file.file_kind === "video" ? "" : classes.minor}`}
            >
              {FILE_KIND_ICONS[file.file_kind] ?? <IconFile size={16} />}
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className={classes.path}>{file.path}</div>
              <Text size="xs" c="dimmed" mt={2}>
                {FILE_KIND_LABELS[file.file_kind]} · {formatSize(file.size)}
              </Text>
            </div>
          </div>
        ))}
      </Paper>
    );

  const panel = (
    <aside className={classes.panel} aria-label="资料">
      <div className={classes.panelHead}>
        <h3>资料</h3>
        <Text size="xs" c="dimmed" ml="auto">
          {allFields.filter((field) => !isEmpty(field.value)).length} /{" "}
          {allFields.length} 项有值
        </Text>
      </div>
      {panelSections.map((section) => (
        <div key={section.name}>
          {panelSections.length > 1 && (
            <div className={classes.group}>{section.name}</div>
          )}
          {section.fields.map((field) => (
            <div
              key={field.key}
              className={`${classes.kv} ${classes.hoverRow}`}
            >
              <span className={classes.kvLabel}>{field.label}</span>
              <div className={classes.kvValue}>
                {field.value_type === "tags" &&
                Array.isArray(field.value) &&
                field.value.length > 0 ? (
                  <TagsValue tags={field.value as string[]} />
                ) : (
                  <FieldValue field={field} />
                )}
              </div>
              {actions(field)}
            </div>
          ))}
        </div>
      ))}
      <div className={classes.hint}>
        <IconInfo size={14} />
        手改的字段会自动锁定，再次刮削不覆盖。
      </div>
    </aside>
  );

  return (
    <div style={{ maxWidth: 1480 }}>
      <Link to={`/libraries/${detail.library_id}`} className={classes.crumb}>
        <IconBack size={14} /> {detail.library_name}
      </Link>
      {hero}
      {player.chooser}
      <div className={classes.body}>
        <Tabs value={tab} onChange={setTab} keepMounted={false}>
          <Tabs.List mb="lg">
            <Tabs.Tab value="overview">概览</Tabs.Tab>
            <Tabs.Tab value="people">
              演职员{" "}
              {peopleCount > 0 && (
                <span className="mo-mono">{peopleCount}</span>
              )}
            </Tabs.Tab>
            <Tabs.Tab value="images">
              图片{" "}
              <span className="mo-mono">
                {imagesFilled}/{imageFields.length}
              </span>
            </Tabs.Tab>
            <Tabs.Tab value="files">
              文件{" "}
              {sourceFiles.length > 0 && (
                <span className="mo-mono">{sourceFiles.length}</span>
              )}
            </Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="overview">{overviewTab}</Tabs.Panel>
          <Tabs.Panel value="people">{peopleTab}</Tabs.Panel>
          <Tabs.Panel value="images">
            {imageFields.length === 0 ? (
              <div className={classes.empty}>这个类型没有图片字段</div>
            ) : (
              <div className={classes.images}>{imageFields.map(imageSlot)}</div>
            )}
          </Tabs.Panel>
          <Tabs.Panel value="files">{filesTab}</Tabs.Panel>
        </Tabs>
        {panel}
      </div>
      {editing !== null && (
        <FieldEditModal
          field={editing}
          saving={edit.isPending}
          error={edit.isError ? edit.error.message : null}
          onSave={(value) => edit.mutate({ key: editing.key, value })}
          onClose={() => setEditing(null)}
        />
      )}
      {rematching && pluginType && (
        <SourceSearchModal
          title="重新匹配"
          dataType={detail.data_type_id}
          initialQuery={detail.title}
          picking={rematch.isPending}
          error={rematch.isError ? rematch.error.message : null}
          onPick={(source, hit) =>
            rematch.mutate({ source, externalId: hit.external_id })
          }
          onClose={() => setRematching(false)}
        />
      )}
      {supplementing && (
        <SourceSearchModal
          title="补充来源"
          dataType={detail.data_type_id}
          initialQuery={originalTitle ?? detail.title}
          exclude={Object.keys(detail.external_ids).slice(0, 1)}
          picking={supplement.isPending}
          error={supplement.isError ? supplement.error.message : null}
          onPick={(source, hit) =>
            supplement.mutate({ source, externalId: hit.external_id })
          }
          onClose={() => setSupplementing(false)}
        />
      )}
      {rematching && !pluginType && (
        <TmdbSearchModal
          title="重新匹配"
          kind={series ? "tv" : "movie"}
          initialQuery={detail.title}
          initialYear={detail.year}
          picking={rematch.isPending}
          error={rematch.isError ? rematch.error.message : null}
          onPick={(hit: MovieHit) => rematch.mutate({ tmdbId: hit.tmdb_id })}
          onClose={() => setRematching(false)}
        />
      )}
      {deleting && (
        <DeleteItemModal
          itemId={detail.id}
          title={detail.title}
          onClose={() => setDeleting(false)}
        />
      )}
      <ArrangePreviewModal
        opened={previewing}
        onClose={() => setPreviewing(false)}
        plan={
          preview.data === undefined
            ? undefined
            : {
                rename: preview.data.rename,
                write: preview.data.write,
                scope: "整理这部电影",
                empty: null,
                previews: [preview.data],
                canConfirm:
                  preview.data.problems.length === 0 &&
                  (preview.data.write || preview.data.changes),
              }
        }
        loading={preview.isFetching}
        error={
          preview.isError
            ? preview.error.message
            : arrange.isError
              ? arrange.error.message
              : null
        }
        onConfirm={() => arrange.mutate()}
        confirming={arrange.isPending}
      />
    </div>
  );
}
