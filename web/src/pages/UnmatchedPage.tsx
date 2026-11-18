import { Alert, Button, Loader, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type MovieHit,
  type SourceHit,
  type UnmatchedFile,
  bindMediaFile,
  bindMediaFileToSource,
  fetchLibraries,
  fetchSettings,
  fetchUnmatched,
  typeSection,
} from "../api";
import { useAgent } from "../components/agent/AgentContext";
import { bindDraft } from "../components/agent/drafts";
import { SourceSearchPanel } from "../components/match/SourceSearchPanel";
import { TmdbSearchPanel } from "../components/match/TmdbSearchPanel";
import { PageHeader, Pill } from "../components/ui";
import { isPluginType, isSeriesType, useDataTypes } from "../dataTypes";
import { formatSize } from "../format";
import { DataTypeIcon, IconCheck, IconSearch, IconSparkle } from "../icons";
import classes from "./UnmatchedPage.module.css";

function splitPath(path: string): [string, string] {
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return [path.slice(0, index), path.slice(index + 1)];
}

export function UnmatchedPage() {
  const queryClient = useQueryClient();
  const agent = useAgent();
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const dataTypes = useDataTypes();
  const [libraryId, setLibraryId] = useState<string | null>(null);
  const selectedLib = libraryId === null ? null : Number(libraryId);
  const files = useQuery({
    queryKey: ["unmatched", selectedLib],
    queryFn: () => fetchUnmatched(selectedLib),
  });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const bind = useMutation({
    mutationFn: ({
      file,
      hit,
      source,
    }: {
      file: UnmatchedFile;
      hit: MovieHit | SourceHit;
      source?: string;
    }) =>
      source !== undefined && "external_id" in hit
        ? bindMediaFileToSource(file.id, source, hit)
        : bindMediaFile(file.id, hit as MovieHit),
    onSuccess: (_result, { file, hit }) => {
      setSelectedId(null);
      void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      void queryClient.invalidateQueries({ queryKey: ["wall", file.library_id] });
      notifications.show({ color: "green", message: `已匹配为《${hit.title}》，正在刮削` });
    },
  });

  const selectFile = (file: UnmatchedFile) => {
    bind.reset();
    setSelectedId(file.id);
  };

  const rows = files.data ?? [];
  const selectedFile = rows.find((file) => file.id === selectedId) ?? null;
  const selectedName = selectedFile !== null ? splitPath(selectedFile.path)[1] : "";
  const selectedTypeName =
    selectedFile !== null
      ? (dataTypes.data?.find((type) => type.id === selectedFile.data_type_id)?.name ??
        selectedFile.data_type_id)
      : "";
  const selectedThreshold =
    selectedFile !== null
      ? typeSection(settings.data?.types, selectedFile.data_type_id).match_threshold
      : 0.75;
  const selectedIsPlugin =
    selectedFile !== null && isPluginType(dataTypes.data, selectedFile.data_type_id);

  return (
    <Stack gap={0}>
      <PageHeader
        title="未识别"
        pills={
          files.isSuccess && rows.length > 0 ? (
            <Pill tone="warn">{rows.length} 条待处理</Pill>
          ) : undefined
        }
        description="扫描时没能自动认出的文件和文件夹，选中一条在右边搜索匹配"
        actions={
          <Select
            placeholder="全部媒体库"
            clearable
            w={220}
            value={libraryId}
            onChange={(value) => {
              setLibraryId(value);
              bind.reset();
            }}
            data={(libraries.data ?? []).map((library) => ({
              value: String(library.id),
              label: library.name,
            }))}
            aria-label="媒体库"
          />
        }
      />

      {files.isPending && <Loader size="sm" />}

      {files.isError && (
        <Alert color="red" title="读不到文件">
          {files.error.message}
        </Alert>
      )}

      {files.isSuccess && rows.length === 0 && (
        <div className={classes.emptyPage}>
          <div className={classes.emptyPageIcon} aria-hidden="true">
            <IconCheck size={24} />
          </div>
          <Text fw={600} fz={15}>
            没有未识别的文件。
          </Text>
          <Text size="sm" c="var(--mo-muted)">
            {selectedLib === null
              ? "所有扫描到的影片和文件夹都已经识别归类。"
              : "当前媒体库下没有待处理条目，可清除筛选查看全部。"}
          </Text>
        </div>
      )}

      {files.isSuccess && rows.length > 0 && (
        <div className={classes.layout}>
          <div className={classes.list}>
            {rows.map((file) => {
              const [, name] = splitPath(file.path);
              const isSelected = selectedFile?.id === file.id;
              const isSeries = isSeriesType(dataTypes.data, file.data_type_id);
              const guessText =
                file.guess_year !== null
                  ? `${file.guess_title}（${file.guess_year}）`
                  : file.guess_title;

              return (
                <div
                  key={file.id}
                  className={[classes.row, isSelected ? classes.rowSelected : ""]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => selectFile(file)}
                >
                  <div className={classes.iconBox} aria-hidden="true">
                    <DataTypeIcon dataTypeId={file.data_type_id} size={17} />
                  </div>

                  <div className={classes.main}>
                    <div className={classes.nameLine}>
                      <span className={classes.name} title={name}>
                        {name}
                      </span>
                      {isSeries && file.video_count > 0 && (
                        <span className={`${classes.videoCount} mo-mono`}>
                          {file.video_count} 个视频
                        </span>
                      )}
                    </div>
                    {guessText !== "" && (
                      <div className={classes.guess} title={guessText}>
                        猜测：{guessText}
                      </div>
                    )}
                    <code className={`${classes.path} mo-mono`} title={file.path}>
                      {file.path}
                    </code>
                  </div>

                  <div className={classes.lib}>
                    <DataTypeIcon dataTypeId={file.data_type_id} size={13} />
                    <span>{file.library_name}</span>
                  </div>

                  <div className={`${classes.size} mo-mono`}>{formatSize(file.size)}</div>

                  <div className={classes.rowActions}>
                    <Button
                      size="xs"
                      variant="subtle"
                      leftSection={<IconSparkle size={12} />}
                      aria-label={`让 AI 识别${name}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        agent.openWithDraft(bindDraft(file));
                      }}
                    >
                      让 AI 识别
                    </Button>
                    <Button
                      size="xs"
                      variant={isSelected ? "filled" : "default"}
                      leftSection={<IconSearch size={12} />}
                      aria-label={`匹配${name}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        selectFile(file);
                      }}
                    >
                      匹配
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>

          <aside className={classes.matchBox} aria-label="匹配面板">
            {selectedFile === null ? (
              <div className={classes.matchEmpty}>
                <div className={classes.emptyIcon} aria-hidden="true">
                  <IconSearch size={20} />
                </div>
                <Text fw={600} fz={15}>
                  选择一条未识别文件
                </Text>
                <Text size="sm" c="var(--mo-muted)" maw={280}>
                  在左侧列表点击任意条目或「匹配」按钮，即可在这里搜索并绑定元数据
                </Text>
              </div>
            ) : (
              <>
                <div className={classes.matchHead}>
                  <h3 className={classes.matchTitle} title={`匹配「${selectedName}」`}>
                    匹配「{selectedName}」
                  </h3>
                  <p className={classes.matchDesc}>{selectedTypeName}</p>
                </div>

                {selectedIsPlugin ? (
                  <SourceSearchPanel
                    key={selectedFile.id}
                    embedded
                    dataType={selectedFile.data_type_id}
                    initialQuery={selectedFile.guess_title}
                    matchThreshold={selectedThreshold}
                    picking={bind.isPending}
                    error={bind.isError ? bind.error.message : null}
                    onPick={(source, hit) => bind.mutate({ file: selectedFile, hit, source })}
                  />
                ) : (
                  <TmdbSearchPanel
                    key={selectedFile.id}
                    embedded
                    kind={
                      isSeriesType(dataTypes.data, selectedFile.data_type_id) ? "tv" : "movie"
                    }
                    initialQuery={selectedFile.guess_title}
                    initialYear={selectedFile.guess_year}
                    matchThreshold={selectedThreshold}
                    picking={bind.isPending}
                    error={bind.isError ? bind.error.message : null}
                    onPick={(hit) => bind.mutate({ file: selectedFile, hit })}
                  />
                )}

                <div className={classes.matchFoot}>
                  <span>同目录下的其余文件会一并归入</span>
                </div>
              </>
            )}
          </aside>
        </div>
      )}
    </Stack>
  );
}
