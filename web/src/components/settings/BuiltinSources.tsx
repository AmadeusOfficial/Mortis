import { Alert, Anchor, Button, Group, NumberInput, Stack, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import {
  type BuiltinSourceInfo,
  type BuiltinSourceUpdate,
  fetchBuiltinSources,
  fetchImdbDataset,
  refreshImdbDataset,
  updateBuiltinSource,
} from "../../api";
import { formatTime } from "../../format";
import styles from "../../pages/SettingsPage.module.css";
import { Pill } from "../ui";
import { SecretInputRow } from "./GeneralSections";
import { SaveBar, SettingCard, SettingRow } from "./SettingLayout";

// 设置页「来源」分组里内置来源的条目，顺序就是导航里的顺序。
export const BUILTIN_SOURCE_ENTRIES: { id: string; label: string }[] = [
  { id: "fanart", label: "Fanart.tv" },
  { id: "tvdb", label: "TVDB" },
  { id: "imdb", label: "IMDb 评分" },
  { id: "ratings_rt", label: "烂番茄" },
];

const TYPE_NAMES: Record<string, string> = { movie: "电影", tv: "电视剧", anime: "动漫" };

export const BUILTIN_SOURCES_KEY = ["builtin-sources"];

export function useBuiltinSources() {
  return useQuery({ queryKey: BUILTIN_SOURCES_KEY, queryFn: fetchBuiltinSources });
}

// 来源能不能用会影响类型页的来源列表、来源顺序和匹配面板。
export function invalidateSourceStatus(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: BUILTIN_SOURCES_KEY });
  void queryClient.invalidateQueries({ queryKey: ["sources"] });
  void queryClient.invalidateQueries({ queryKey: ["source-orders"] });
}

function useSourceUpdate(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: BuiltinSourceUpdate) => updateBuiltinSource(id, body),
    onSuccess: (saved) => {
      queryClient.setQueryData<BuiltinSourceInfo[]>(BUILTIN_SOURCES_KEY, (old) =>
        old?.map((source) => (source.id === saved.id ? saved : source)),
      );
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
      void queryClient.invalidateQueries({ queryKey: ["source-orders"] });
    },
  });
}

export function BuiltinSourceSection({ sourceId }: { sourceId: string }) {
  if (sourceId === "fanart") {
    return <FanartSection />;
  }
  if (sourceId === "tvdb") {
    return <TvdbSection />;
  }
  if (sourceId === "imdb") {
    return <ImdbSection />;
  }
  return <RtSection />;
}

export function isBuiltinSourceSection(sourceId: string): boolean {
  return BUILTIN_SOURCE_ENTRIES.some((entry) => entry.id === sourceId);
}

function SourceShell({
  sourceId,
  children,
}: {
  sourceId: string;
  children: (source: BuiltinSourceInfo) => ReactNode;
}) {
  const sources = useBuiltinSources();
  const source = sources.data?.find((item) => item.id === sourceId);
  if (sources.isError) {
    return (
      <Alert color="red" title="读不到来源设置">
        {sources.error.message}
      </Alert>
    );
  }
  if (source === undefined) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }
  return <Stack gap="md">{children(source)}</Stack>;
}

function StatusPill({ source }: { source: BuiltinSourceInfo }) {
  return (
    <Pill tone={source.available ? "ok" : "bad"}>
      {source.available ? "可用" : `不可用：${source.reason ?? "未知原因"}`}
    </Pill>
  );
}

function usedFor(source: BuiltinSourceInfo): string {
  const names = ["movie", "tv", "anime"]
    .filter((id) => source.data_types.includes(id))
    .map((id) => TYPE_NAMES[id]);
  return `用于：${names.join("、")}`;
}

function SourceSecret({
  source,
  secretKey,
  label,
  hint,
}: {
  source: BuiltinSourceInfo;
  secretKey: string;
  label: string;
  hint?: string;
}) {
  const save = useSourceUpdate(source.id);
  return (
    <SecretInputRow
      label={label}
      hintExtra={hint}
      isSet={source.secrets[secretKey] ?? false}
      loadError={null}
      saving={save.isPending}
      error={save.error}
      onSave={(next, done) =>
        save.mutate({ secrets: { [secretKey]: next ?? "" } }, { onSuccess: done })
      }
    />
  );
}

function FanartSection() {
  return (
    <SourceShell sourceId="fanart">
      {(source) => (
        <SettingCard
          title="Fanart.tv"
          description={`${usedFor(source)}。只出图片：横幅、透明艺术图、光盘、缩略图等。`}
          actions={<StatusPill source={source} />}
        >
          <div className={styles.fieldList}>
            <SourceSecret
              source={source}
              secretKey="api_key"
              label="项目 Key"
              hint="必填，在 fanart.tv 申请。"
            />
            <SourceSecret
              source={source}
              secretKey="client_key"
              label="个人 Key"
              hint="选填，填了能更早拿到新图。"
            />
          </div>
        </SettingCard>
      )}
    </SourceShell>
  );
}

function TvdbSection() {
  return (
    <SourceShell sourceId="tvdb">
      {(source) => (
        <SettingCard
          title="TVDB"
          description={`${usedFor(source)}。默认只给图片，文本字段要在来源顺序里打开。`}
          actions={<StatusPill source={source} />}
        >
          <div className={styles.fieldList}>
            <SourceSecret source={source} secretKey="api_key" label="TVDB API Key" hint="必填。" />
            <SourceSecret
              source={source}
              secretKey="pin"
              label="PIN"
              hint="订阅用户的 PIN，选填。"
            />
          </div>
        </SettingCard>
      )}
    </SourceShell>
  );
}

// 一个来源的普通设置：草稿、改动数、保存后用返回值覆盖草稿。
function useSourceSettings<T extends Record<string, unknown>>(source: BuiltinSourceInfo) {
  const saved = source.settings as T;
  const [draft, setDraft] = useState<T>(saved);
  const save = useSourceUpdate(source.id);
  const dirtyCount = Object.keys(saved).filter((key) => saved[key] !== draft[key]).length;
  const submit = (label: string, values: T) =>
    save.mutate(
      { settings: values },
      {
        onSuccess: (next) => {
          setDraft(next.settings as T);
          notifications.show({ color: "green", message: `${label}已保存` });
        },
      },
    );
  const discard = () => {
    setDraft(saved);
    save.reset();
  };
  return { saved, draft, setDraft, save, dirtyCount, submit, discard };
}

type ImdbSettings = { enabled: boolean; refresh_days: number };

function ImdbSection() {
  return <SourceShell sourceId="imdb">{(source) => <ImdbForm source={source} />}</SourceShell>;
}

function ImdbForm({ source }: { source: BuiltinSourceInfo }) {
  const { draft, setDraft, save, dirtyCount, submit, discard } =
    useSourceSettings<ImdbSettings>(source);
  const [days, setDays] = useState<number | "">(draft.refresh_days);
  const enabledId = useId();
  const daysId = useId();
  const daysHintId = useId();
  const daysBad = days === "" || days < 1 || days > 90;
  const changes = dirtyCount + (days !== draft.refresh_days ? 1 : 0);

  return (
    <>
      <SettingCard
        title="IMDb 评分"
        description={`${usedFor(source)}。用 IMDb 官方公开的评分数据集，不需要 Key，整份下载到数据目录，刮削时按 IMDb 编号查。`}
        actions={<StatusPill source={source} />}
      >
        <div className={styles.fieldList}>
          <SettingRow label="启用" htmlFor={enabledId}>
            <Switch
              id={enabledId}
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.currentTarget.checked })}
            />
          </SettingRow>
          <SettingRow
            label="更新间隔"
            hint="数据导入超过这么多天，下次启动时自动更新。1 到 90 天。"
            htmlFor={daysId}
            hintId={daysHintId}
          >
            <NumberInput
              id={daysId}
              aria-describedby={daysHintId}
              className={styles.monoInput}
              maw={160}
              min={1}
              max={90}
              allowDecimal={false}
              suffix=" 天"
              value={days}
              onChange={(value) => setDays(typeof value === "number" ? value : "")}
              error={daysBad ? "要在 1 到 90 之间" : undefined}
            />
          </SettingRow>
        </div>
      </SettingCard>
      <ImdbDatasetCard />
      <SaveBar
        dirtyCount={changes}
        saveLabel="保存 IMDb 设置"
        saving={save.isPending}
        disabled={daysBad}
        error={save.error}
        onDiscard={() => {
          discard();
          setDays(source.settings.refresh_days as number);
        }}
        onSave={() => {
          if (days !== "") {
            submit("IMDb 设置", { ...draft, refresh_days: days });
          }
        }}
      />
    </>
  );
}

function ImdbDatasetCard() {
  const queryClient = useQueryClient();
  const dataset = useQuery({
    queryKey: ["imdb-dataset"],
    queryFn: fetchImdbDataset,
    refetchInterval: (query) => (query.state.data?.task_id != null ? 2000 : false),
  });
  const refresh = useMutation({
    mutationFn: refreshImdbDataset,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["imdb-dataset"] }),
  });
  const taskId = dataset.data?.task_id ?? null;
  const lastTask = useRef<number | null>(null);
  useEffect(() => {
    // 更新任务结束时数据可能刚下载好，来源从「还没下载」变成可用。
    if (lastTask.current !== null && taskId === null) {
      invalidateSourceStatus(queryClient);
    }
    lastTask.current = taskId;
  }, [taskId, queryClient]);
  const running = taskId !== null || refresh.isPending;

  return (
    <SettingCard
      title="评分数据"
      description="来自 datasets.imdbws.com 的 title.ratings，存成数据目录下的缓存文件，删了会重新下载。"
      actions={
        dataset.data === undefined ? undefined : running ? (
          <Pill tone="accent">正在更新…</Pill>
        ) : dataset.data.count === null ? (
          <Pill tone="warn">还没下载</Pill>
        ) : dataset.data.stale ? (
          <Pill tone="warn">已过期</Pill>
        ) : (
          <Pill tone="ok">最新</Pill>
        )
      }
    >
      {dataset.isError && (
        <div className={styles.cardBody}>
          <Alert color="red" title="读不到评分数据状态">
            {dataset.error.message}
          </Alert>
        </div>
      )}
      {dataset.data !== undefined && (
        <div className={styles.fieldList}>
          <SettingRow label="导入时间">
            <Text size="sm" className="mo-mono">
              {formatTime(dataset.data.imported_at)}
            </Text>
          </SettingRow>
          <SettingRow label="条数">
            <Text size="sm" className="mo-mono" aria-label="IMDb 评分条数">
              {dataset.data.count === null ? "—" : dataset.data.count.toLocaleString("zh-CN")}
            </Text>
          </SettingRow>
        </div>
      )}
      <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
        <Stack gap="sm">
          <Group gap="sm" wrap="wrap">
            <Button
              variant="default"
              loading={running}
              disabled={dataset.data === undefined}
              onClick={() => refresh.mutate()}
            >
              立即更新
            </Button>
            {taskId !== null && (
              <Anchor component={Link} to="/tasks" size="sm" fw={600}>
                在任务页看进度
              </Anchor>
            )}
          </Group>
          {refresh.isError && (
            <Alert color="red" title="排不了更新任务">
              {refresh.error.message}
            </Alert>
          )}
        </Stack>
      </div>
    </SettingCard>
  );
}

type RtSettings = { enabled: boolean };

function RtSection() {
  return <SourceShell sourceId="ratings_rt">{(source) => <RtForm source={source} />}</SourceShell>;
}

function RtForm({ source }: { source: BuiltinSourceInfo }) {
  const { draft, setDraft, save, dirtyCount, submit, discard } =
    useSourceSettings<RtSettings>(source);
  const enabledId = useId();

  return (
    <>
      <SettingCard
        title="烂番茄"
        description={`${usedFor(source)}。按 IMDb 编号取影评人新鲜度。默认查 Wikidata，不需要 Key；填了 MDBList 的 Key 就改用 MDBList。`}
        actions={<StatusPill source={source} />}
      >
        <div className={styles.fieldList}>
          <SettingRow label="启用" htmlFor={enabledId}>
            <Switch
              id={enabledId}
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.currentTarget.checked })}
            />
          </SettingRow>
          <SettingRow label="当前使用" hint="由下面的 MDBList Key 决定">
            <Pill tone="neutral">{source.provider ?? "Wikidata"}</Pill>
          </SettingRow>
          <SourceSecret
            source={source}
            secretKey="mdblist_api_key"
            label="MDBList Key"
            hint="选填，mdblist.com 的免费 Key。"
          />
        </div>
      </SettingCard>
      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel="保存烂番茄设置"
        saving={save.isPending}
        error={save.error}
        onDiscard={discard}
        onSave={() => submit("烂番茄设置", draft)}
      />
    </>
  );
}
