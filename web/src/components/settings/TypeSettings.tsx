import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Paper,
  Stack,
  Text,
  Title,
} from "@mantine/core";
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
  fetchSecretsStatus,
  fetchSettings,
  saveTypeSettings,
  typeSection,
} from "../../api";
import { useDataTypes } from "../../dataTypes";
import { SourceOrdersSection } from "../SourceOrdersSection";
import { SaveError } from "./GeneralSections";
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

// 一种数据类型一页：识别和扫描、刮削、整理、来源、来源顺序，最后整节保存。
export function TypeSettings({ dataTypeId }: { dataTypeId: string }) {
  const queryClient = useQueryClient();
  const dataTypes = useDataTypes();
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const [draft, setDraft] = useState<TypeSection | null>(null);
  const [thresholdText, setThresholdText] = useState("");
  const [skipText, setSkipText] = useState("");

  const typeName = dataTypes.data?.find((type) => type.id === dataTypeId)?.name ?? dataTypeId;
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
  if (draft === null) {
    return <Text c="dimmed">正在读取设置…</Text>;
  }

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

  const setDraftValues = (patch: Partial<TypeSection>) => setDraft({ ...draft, ...patch });

  return (
    <Stack>
      <Title order={3}>{typeName} 设置</Title>
      <Paper withBorder p="md">
        <Stack>
          <Title order={4}>识别和扫描</Title>
          <ScanFields
            thresholdText={thresholdText}
            onThresholdText={setThresholdText}
            skipText={skipText}
            onSkipText={setSkipText}
          />
        </Stack>
      </Paper>

      {isMovieSection(draft) && (
        <>
          <Paper withBorder p="md">
            <Stack>
              <Title order={4}>刮削</Title>
              <MovieScrapeFields movie={draft} onChange={setDraftValues} />
            </Stack>
          </Paper>
          <Paper withBorder p="md">
            <Stack>
              <Title order={4}>整理</Title>
              <MovieOrganizeFields
                output={draft.output}
                onChange={(patch: Partial<MovieOutputConfig>) =>
                  setDraftValues({ output: { ...draft.output, ...patch } })
                }
              />
            </Stack>
          </Paper>
        </>
      )}

      {isSeriesSection(draft) && (
        <>
          <Paper withBorder p="md">
            <Stack>
              <Title order={4}>刮削</Title>
              <SeriesScrapeFields series={draft} onChange={setDraftValues} />
            </Stack>
          </Paper>
          <Paper withBorder p="md">
            <Stack>
              <Title order={4}>整理</Title>
              <SeriesOrganizeFields
                output={draft.output}
                onChange={(patch: Partial<TvOutputConfig>) =>
                  setDraftValues({ output: { ...draft.output, ...patch } })
                }
              />
            </Stack>
          </Paper>
        </>
      )}

      <SaveError error={save.error} />
      <Group>
        <Button
          loading={save.isPending}
          disabled={thresholdBad || countryBad}
          onClick={submit}
        >
          保存{typeName}设置
        </Button>
      </Group>

      <TypeSources dataTypeId={dataTypeId} />

      {/* 来源顺序单独保存；内置类型没有来源顺序时这个组件返回空。 */}
      <SourceOrdersSection dataTypeId={dataTypeId} />
    </Stack>
  );
}

// 这个类型用的来源：内置类型是 TMDB，插件类型是服务它的来源插件。
function TypeSources({ dataTypeId }: { dataTypeId: string }) {
  const dataTypes = useDataTypes();
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins });
  const type = dataTypes.data?.find((item) => item.id === dataTypeId);
  const builtin = type !== undefined && type.plugin_id === null;

  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>来源</Title>
        {builtin ? (
          <Stack gap={4}>
            <Group gap="xs">
              <Text size="sm">TMDB</Text>
              <Badge size="sm" variant="light" color={secrets.data?.tmdb_api_key ? "green" : "yellow"}>
                {secrets.data === undefined
                  ? "正在读取…"
                  : secrets.data.tmdb_api_key
                    ? "Key 已填"
                    : "Key 未填"}
              </Badge>
            </Group>
            <Anchor component={Link} to="/settings?section=source.tmdb" size="sm">
              去设置 TMDB
            </Anchor>
          </Stack>
        ) : (
          <>
            {plugins.isError && (
              <Alert color="red" title="读不到插件">
                {plugins.error.message}
              </Alert>
            )}
            {plugins.isPending && <Text c="dimmed">正在读取来源…</Text>}
            {plugins.isSuccess &&
              plugins.data.filter(
                (plugin) => plugin.kind === "source" && plugin.data_type === dataTypeId,
              ).length === 0 && <Text c="dimmed">这个类型还没有来源插件。</Text>}
            {plugins.data
              ?.filter((plugin) => plugin.kind === "source" && plugin.data_type === dataTypeId)
              .map((plugin) => {
                const reason = !plugin.enabled
                  ? "已停用"
                  : plugin.error !== null
                    ? plugin.error
                    : plugin.notice;
                return (
                  <Stack key={plugin.id} gap={4}>
                    <Group gap="xs">
                      <Text size="sm">{plugin.name}</Text>
                      <Badge size="sm" variant="light" color={reason === null ? "green" : "yellow"}>
                        {reason === null ? "可用" : "不可用"}
                      </Badge>
                    </Group>
                    {reason !== null && (
                      <Text size="xs" c="dimmed">
                        {reason}
                      </Text>
                    )}
                    <Anchor component={Link} to={`/settings?section=source.${plugin.id}`} size="sm">
                      去设置 {plugin.name}
                    </Anchor>
                  </Stack>
                );
              })}
          </>
        )}
      </Stack>
    </Paper>
  );
}
