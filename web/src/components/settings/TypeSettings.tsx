import { Alert, Anchor, Group, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  type MovieOutputConfig,
  type MovieScrapeConfig,
  type TvOutputConfig,
  type TvScrapeConfig,
  type TypeSection,
  fetchPlugins,
  fetchSettings,
  fetchSources,
  saveTypeSettings,
  typeSection,
} from "../../api";
import { useDataTypes } from "../../dataTypes";
import styles from "../../pages/SettingsPage.module.css";
import { SourceOrdersSection } from "../SourceOrdersSection";
import { Pill } from "../ui";
import { PlayerCard } from "./PlaybackSection";
import { SaveBar, SettingCard } from "./SettingLayout";
import {
  MovieOrganizeFields,
  MovieScrapeFields,
  ScanFields,
  SeriesOrganizeFields,
  SeriesScrapeFields,
  countryInvalid,
  formatThreshold,
  parseSkipFolders,
  thresholdInvalid,
} from "./TypeFields";

// 电影和剧集类的设置结构不同：整理块里的目录模板名不一样。
function isMovieSection(value: TypeSection): value is MovieScrapeConfig {
  return "output" in value && "folder_template" in (value.output as object);
}

function isSeriesSection(value: TypeSection): value is TvScrapeConfig {
  return "output" in value && "show_template" in (value.output as object);
}

function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

function countTypeChanges(
  saved: TypeSection,
  draft: TypeSection,
  thresholdText: string,
  skipText: string,
): number {
  let count = 0;
  const parsedThreshold = Number(thresholdText);
  const thresholdSame =
    thresholdText.trim() !== "" &&
    !Number.isNaN(parsedThreshold) &&
    parsedThreshold === saved.match_threshold;
  if (!thresholdSame) {
    count += 1;
  }
  if (!arraysEqual(parseSkipFolders(skipText), saved.skip_folders)) {
    count += 1;
  }
  const savedRec = saved as unknown as Record<string, unknown>;
  const draftRec = draft as unknown as Record<string, unknown>;
  for (const key of Object.keys(savedRec)) {
    if (key === "match_threshold" || key === "skip_folders") {
      continue;
    }
    const sv = savedRec[key];
    const dv = draftRec[key];
    if (typeof sv === "object" && sv !== null) {
      if (JSON.stringify(sv) !== JSON.stringify(dv)) {
        count += 1;
      }
    } else if (sv !== dv) {
      count += 1;
    }
  }
  return count;
}

// 一种数据类型一页：识别和扫描、刮削、整理、来源、来源顺序，最后整节保存。
export function TypeSettings({ dataTypeId }: { dataTypeId: string }) {
  const queryClient = useQueryClient();
  const dataTypes = useDataTypes();
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const [draft, setDraft] = useState<TypeSection | null>(null);
  const [thresholdText, setThresholdText] = useState("");
  const [skipText, setSkipText] = useState("");

  const typeInfo = dataTypes.data?.find((type) => type.id === dataTypeId);
  const typeName = typeInfo?.name ?? dataTypeId;
  const isBuiltin = typeInfo !== undefined && typeInfo.plugin_id === null;
  const loadedFor = useRef<string | null>(null);

  useEffect(() => {
    // 只在这个类型第一次读到时填草稿；后台重新拉取不能冲掉正在改的内容。
    if (settings.data === undefined || loadedFor.current === dataTypeId) {
      return;
    }
    loadedFor.current = dataTypeId;
    const value = typeSection(settings.data.types, dataTypeId);
    setDraft(value);
    setThresholdText(formatThreshold(value.match_threshold));
    setSkipText(value.skip_folders.join("\n"));
  }, [settings.data, dataTypeId]);

  const save = useMutation({
    mutationFn: (value: TypeSection) => saveTypeSettings(dataTypeId, value),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      const value = typeSection(saved.types, dataTypeId);
      setDraft(value);
      setThresholdText(formatThreshold(value.match_threshold));
      setSkipText(value.skip_folders.join("\n"));
      notifications.show({ color: "green", message: `${typeName}设置已保存` });
    },
  });

  if (settings.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {settings.error.message}
      </Alert>
    );
  }
  if (draft === null || settings.data === undefined) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }

  const savedValue = typeSection(settings.data.types, dataTypeId);
  const dirtyCount = countTypeChanges(savedValue, draft, thresholdText, skipText);

  const threshold = Number(thresholdText);
  const thresholdBad = thresholdInvalid(thresholdText);
  const country = "certification_country" in draft ? draft.certification_country : "US";
  const countryBad = "certification_country" in draft && countryInvalid(country);

  const submit = () => {
    if (thresholdBad) {
      return;
    }
    save.mutate({
      ...draft,
      match_threshold: threshold,
      skip_folders: parseSkipFolders(skipText),
    });
  };

  const discard = () => {
    setDraft(savedValue);
    setThresholdText(formatThreshold(savedValue.match_threshold));
    setSkipText(savedValue.skip_folders.join("\n"));
    save.reset();
  };

  const setDraftValues = (patch: Partial<TypeSection>) => setDraft({ ...draft, ...patch });

  return (
    <Stack gap="md">
      <div className={styles.typeHeader}>
        <Title order={3} fz={18}>
          {typeName} 设置
        </Title>
        {isBuiltin && <Pill tone="neutral">TMM 兼容选项</Pill>}
      </div>

      <SettingCard
        title="识别和扫描"
        description="目录名和来源结果的相似度达到阈值才自动认定；跳过的目录在扫描时整个不看。"
      >
        <ScanFields
          thresholdText={thresholdText}
          onThresholdText={setThresholdText}
          skipText={skipText}
          onSkipText={setSkipText}
        />
      </SettingCard>

      {isMovieSection(draft) && (
        <>
          <SettingCard title="刮削" description="元数据语言、分级与图片下载偏好">
            <MovieScrapeFields movie={draft} onChange={setDraftValues} />
          </SettingCard>
          <SettingCard
            title="整理"
            description="刮削只改莫提斯里的资料；整理才动磁盘，让 Emby、Plex、Kodi 能直接读到。"
          >
            <MovieOrganizeFields
              output={draft.output}
              onChange={(patch: Partial<MovieOutputConfig>) =>
                setDraftValues({ output: { ...draft.output, ...patch } })
              }
            />
          </SettingCard>
        </>
      )}

      {isSeriesSection(draft) && (
        <>
          <SettingCard title="刮削" description="元数据语言、分级与图片下载偏好">
            <SeriesScrapeFields series={draft} onChange={setDraftValues} />
          </SettingCard>
          <SettingCard
            title="整理"
            description="刮削只改莫提斯里的资料；整理才动磁盘，让 Emby、Plex、Kodi 能直接读到。"
          >
            <SeriesOrganizeFields
              output={draft.output}
              onChange={(patch: Partial<TvOutputConfig>) =>
                setDraftValues({ output: { ...draft.output, ...patch } })
              }
            />
          </SettingCard>
        </>
      )}

      <TypeSources dataTypeId={dataTypeId} />

      {typeInfo?.plugin_id != null && <TypePlayers pluginId={typeInfo.plugin_id} />}

      {/* 来源顺序单独保存，不跟着下面的保存栏。 */}
      <SourceOrdersSection dataTypeId={dataTypeId} />

      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel={`保存${typeName}设置`}
        saving={save.isPending}
        disabled={thresholdBad || countryBad}
        error={save.error}
        onDiscard={discard}
        onSave={submit}
      />
    </Stack>
  );
}

function SourceStatusRow({
  id,
  name,
  reason,
}: {
  id: string;
  name: string;
  reason: string | null;
}) {
  return (
    <div className={styles.listRow}>
      <Group gap="xs" wrap="wrap" style={{ minWidth: 0 }}>
        <Text size="sm" fw={600}>
          {name}
        </Text>
        <Pill tone={reason === null ? "ok" : "bad"}>{reason === null ? "可用" : "不可用"}</Pill>
        {reason !== null && (
          <Text size="xs" c="var(--mo-dim)">
            {reason}
          </Text>
        )}
      </Group>
      <Anchor
        component={Link}
        to={`/settings?section=source.${id}`}
        size="sm"
        fw={600}
        style={{ flexShrink: 0 }}
      >
        去设置
      </Anchor>
    </div>
  );
}

// 插件类型自带的播放插件（如 Galgame 启动），各自单独保存。
function TypePlayers({ pluginId }: { pluginId: string }) {
  const queryClient = useQueryClient();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins });
  const players = (plugins.data ?? []).filter(
    (plugin) => plugin.kind === "player" && plugin.parent === pluginId,
  );
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["plugins"] });
  return players.map((plugin) => (
    <PlayerCard key={plugin.id} plugin={plugin} onChanged={refresh} />
  ));
}

// 这个类型用的来源：内置类型是内置来源，插件类型是服务它的来源插件。
function TypeSources({ dataTypeId }: { dataTypeId: string }) {
  const dataTypes = useDataTypes();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins });
  const type = dataTypes.data?.find((item) => item.id === dataTypeId);
  const builtin = type !== undefined && type.plugin_id === null;
  const sources = useQuery({
    queryKey: ["sources", dataTypeId],
    queryFn: () => fetchSources(dataTypeId),
    enabled: builtin,
  });

  return (
    <SettingCard title="来源" description="当前数据类型可用的元数据与图片刮削来源">
      {builtin ? (
        <>
          {sources.isError && (
            <div className={styles.cardBody}>
              <Alert color="red" title="读不到来源">
                {sources.error.message}
              </Alert>
            </div>
          )}
          {sources.isPending && (
            <div className={styles.cardBody}>
              <Text size="sm" c="var(--mo-dim)">
                正在读取来源…
              </Text>
            </div>
          )}
          {sources.data
            ?.filter((source) => source.builtin === true)
            .map((source) => (
              <SourceStatusRow
                key={source.id}
                id={source.id}
                name={source.name}
                reason={source.available === false ? (source.reason ?? "未知原因") : null}
              />
            ))}
        </>
      ) : (
        <>
          {plugins.isError && (
            <div className={styles.cardBody}>
              <Alert color="red" title="读不到插件">
                {plugins.error.message}
              </Alert>
            </div>
          )}
          {plugins.isPending && (
            <div className={styles.cardBody}>
              <Text size="sm" c="var(--mo-dim)">
                正在读取来源…
              </Text>
            </div>
          )}
          {plugins.isSuccess &&
            plugins.data.filter(
              (plugin) => plugin.kind === "source" && plugin.data_type === dataTypeId,
            ).length === 0 && (
              <div className={styles.cardBody}>
                <Text size="sm" c="var(--mo-dim)">
                  这个类型还没有来源插件。
                </Text>
              </div>
            )}
          {plugins.data
            ?.filter((plugin) => plugin.kind === "source" && plugin.data_type === dataTypeId)
            .map((plugin) => {
              const reason = !plugin.enabled
                ? "已停用"
                : plugin.error !== null
                  ? plugin.error
                  : plugin.notice;
              return (
                <SourceStatusRow
                  key={plugin.id}
                  id={plugin.id}
                  name={plugin.name}
                  reason={reason}
                />
              );
            })}
        </>
      )}
    </SettingCard>
  );
}
