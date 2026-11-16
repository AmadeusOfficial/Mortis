import {
  Alert,
  Anchor,
  BackgroundImage,
  Badge,
  Button,
  Divider,
  Group,
  Image,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import {
  type FieldOut,
  type ItemDetail,
  type MovieHit,
  arrangeItem,
  fetchItem,
  fetchItemArrangePreview,
  rematchItem,
  scrapeItem,
  updateItemFields,
  updateItemLocks,
} from "../api";
import { ArrangePreviewModal } from "../components/ArrangePreviewModal";
import { FieldEditModal } from "../components/FieldEditModal";
import { FieldValue } from "../components/FieldValue";
import { PosterCard } from "../components/PosterCard";
import { TmdbSearchModal } from "../components/TmdbSearchModal";
import { formatSize, formatTime } from "../format";
import { useTaskWatcher } from "../useTaskWatcher";

const SOURCE_LABELS: Record<string, string> = { tmdb: "TMDB", manual: "手动" };

function externalLink(provider: string, id: string, kind: string): string | null {
  if (provider === "tmdb") {
    return `https://www.themoviedb.org/${kind === "collection" ? "collection" : "movie"}/${id}`;
  }
  if (provider === "imdb") {
    return `https://www.imdb.com/title/${id}/`;
  }
  return null;
}

// 只列视频和字幕；写出的 NFO、图片、演员头像不在这里显示。
const FILE_KIND_LABELS: Record<string, string> = { video: "视频", subtitle: "字幕" };

export function ItemDetailPage() {
  const id = Number(useParams().itemId);
  const queryClient = useQueryClient();
  const item = useQuery({ queryKey: ["item", id], queryFn: () => fetchItem(id) });
  const [editing, setEditing] = useState<FieldOut | null>(null);
  const [rematching, setRematching] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [arranging, setArranging] = useState(false);
  const setDetail = (detail: ItemDetail) => {
    queryClient.setQueryData(["item", id], detail);
    void queryClient.invalidateQueries({ queryKey: ["wall", detail.library_id] });
  };
  const watcher = useTaskWatcher((task) => {
    setArranging(false);
    void queryClient.invalidateQueries({ queryKey: ["item", id] });
    void queryClient.invalidateQueries({ queryKey: ["item-arrange-preview", id] });
    void queryClient.invalidateQueries({ queryKey: ["wall"] });
    notifications.show(
      task.status === "succeeded"
        ? { color: "green", message: `${task.type_label}完成` }
        : { color: "red", title: `${task.type_label}失败`, message: task.error ?? "任务没有完成" },
    );
  });
  const scrape = useMutation({
    mutationFn: () => scrapeItem(id, true),
    onSuccess: ({ task_id }) => watcher.watch(task_id),
    onError: (error) => notifications.show({ color: "red", message: error.message }),
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
    mutationFn: (hit: MovieHit) => rematchItem(id, hit.tmdb_id),
    onSuccess: ({ task_id }) => {
      setRematching(false);
      watcher.watch(task_id);
      notifications.show({ message: "已换成新的电影，正在刮削" });
    },
  });
  const edit = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) =>
      updateItemFields(id, { [key]: value }),
    onSuccess: (detail) => {
      setDetail(detail);
      notifications.show({ color: "green", message: `已保存「${editing?.label ?? ""}」并锁定` });
      setEditing(null);
    },
  });
  const locks = useMutation({
    mutationFn: (keys: string[]) => updateItemLocks(id, keys),
    onSuccess: setDetail,
    onError: (error) => notifications.show({ color: "red", message: error.message }),
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
  const fields = detail.sections.flatMap((section) => section.fields);
  const lockedKeys = fields.filter((field) => field.locked).map((field) => field.key);
  const toggleLock = (field: FieldOut) =>
    locks.mutate(
      field.locked ? lockedKeys.filter((key) => key !== field.key) : [...lockedKeys, field.key],
    );
  const busy = watcher.watching || scrape.isPending;
  const hasVideo = detail.files.some((file) => file.file_kind === "video");
  const sourceFiles = detail.files.filter((file) => file.file_kind in FILE_KIND_LABELS);
  const canArrange = detail.kind === "work" && hasVideo && detail.scraped_at !== null;

  const header = (
    <Group align="flex-start" wrap="nowrap" p="md">
      {detail.poster_url !== null ? (
        <Image src={detail.poster_url} alt={detail.title} w={200} radius="md" />
      ) : (
        <Paper w={200} h={300} bg="gray.1" radius="md" />
      )}
      <Stack gap="xs">
        <Title order={2}>{detail.title}</Title>
        {detail.subtitle !== null && <Text c="dimmed">{detail.subtitle}</Text>}
        {detail.year !== null && <Text>{detail.year}</Text>}
        <Group gap="xs">
          {Object.entries(detail.external_ids).map(([provider, value]) => {
            const href = externalLink(provider, value, detail.kind);
            const badge = (
              <Badge variant="outline">
                {provider.toUpperCase()} {value}
              </Badge>
            );
            return href !== null ? (
              <Anchor key={provider} href={href} target="_blank" rel="noreferrer">
                {badge}
              </Anchor>
            ) : (
              <span key={provider}>{badge}</span>
            );
          })}
        </Group>
        {detail.collection !== null && (
          <Anchor component={Link} to={`/items/${detail.collection.id}`}>
            系列：{detail.collection.title}
          </Anchor>
        )}
        <Text size="sm" c="dimmed">
          {detail.scraped_at !== null ? `上次刮削 ${formatTime(detail.scraped_at)}` : "还没刮削"}
        </Text>
        {detail.kind === "work" && (
          <Group gap="md">
            <Group gap="xs">
              <Tooltip label="从 TMDB 重新拿资料，锁定的字段不动；按设置刮完会自动整理">
                <Button
                  loading={busy && !arranging}
                  disabled={busy}
                  onClick={() => scrape.mutate()}
                >
                  刮削
                </Button>
              </Tooltip>
              <Tooltip label="认错了电影时用，搜一部对的换上去再刮削">
                <Button
                  variant="default"
                  disabled={busy}
                  onClick={() => {
                    rematch.reset();
                    setRematching(true);
                  }}
                >
                  换一部匹配
                </Button>
              </Tooltip>
            </Group>
            {canArrange && (
              <>
                <Divider orientation="vertical" />
                <Tooltip label="按设置改名，并把 NFO 和图片写到视频旁边，让 Emby、Plex 直接读到">
                  <Button
                    variant="default"
                    loading={arranging}
                    disabled={busy}
                    onClick={() => {
                      arrange.reset();
                      setPreviewing(true);
                    }}
                  >
                    整理
                  </Button>
                </Tooltip>
              </>
            )}
          </Group>
        )}
      </Stack>
    </Group>
  );

  return (
    <Stack maw={1100}>
      <Anchor component={Link} to={`/libraries/${detail.library_id}`} size="sm">
        ← {detail.library_name}
      </Anchor>
      {detail.fanart_url !== null ? (
        <BackgroundImage src={detail.fanart_url} radius="md">
          <div
            style={{
              background: "rgba(255,255,255,0.85)",
              borderRadius: "var(--mantine-radius-md)",
            }}
          >
            {header}
          </div>
        </BackgroundImage>
      ) : (
        <Paper withBorder>{header}</Paper>
      )}
      {detail.parts.length > 0 && (
        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            系列里的电影
          </Title>
          <SimpleGrid cols={{ base: 2, sm: 4, lg: 6 }}>
            {detail.parts.map((part) => (
              <PosterCard key={part.id} item={part} />
            ))}
          </SimpleGrid>
        </Paper>
      )}
      {detail.sections.map((section) => (
        <Paper key={section.name} withBorder p="md">
          <Title order={4} mb="sm">
            {section.name}
          </Title>
          <Table verticalSpacing="xs">
            <Table.Tbody>
              {section.fields.map((field) => (
                <Table.Tr key={field.key}>
                  <Table.Td w={120}>
                    <Text size="sm" fw={500}>
                      {field.label}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <FieldValue field={field} />
                  </Table.Td>
                  <Table.Td w={70}>
                    {field.source !== null && (
                      <Badge
                        size="sm"
                        variant="light"
                        color={field.source === "manual" ? "orange" : "blue"}
                      >
                        {SOURCE_LABELS[field.source] ?? field.source}
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td w={140}>
                    <Group gap={4} wrap="nowrap">
                      {field.editable && (
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          aria-label={`编辑${field.label}`}
                          onClick={() => {
                            edit.reset();
                            setEditing(field);
                          }}
                        >
                          编辑
                        </Button>
                      )}
                      {field.lockable && (
                        <Button
                          size="compact-xs"
                          variant={field.locked ? "light" : "subtle"}
                          color={field.locked ? "orange" : "gray"}
                          aria-label={`${field.locked ? "解锁" : "锁定"}${field.label}`}
                          disabled={locks.isPending}
                          onClick={() => toggleLock(field)}
                        >
                          {field.locked ? "解锁" : "锁定"}
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      ))}
      {sourceFiles.length > 0 && (
        <Paper withBorder p="md">
          <Title order={4} mb="sm">
            文件
          </Title>
          {sourceFiles.map((file) => (
            <Group key={file.id} gap="xs">
              <Badge size="sm" variant="outline">
                {FILE_KIND_LABELS[file.file_kind]}
              </Badge>
              <Text size="sm" ff="monospace">
                {file.path}
              </Text>
              <Text size="xs" c="dimmed">
                {formatSize(file.size)}
              </Text>
            </Group>
          ))}
        </Paper>
      )}
      {editing !== null && (
        <FieldEditModal
          field={editing}
          saving={edit.isPending}
          error={edit.isError ? edit.error.message : null}
          onSave={(value) => edit.mutate({ key: editing.key, value })}
          onClose={() => setEditing(null)}
        />
      )}
      {rematching && (
        <TmdbSearchModal
          title="换一部匹配"
          initialQuery={detail.title}
          initialYear={detail.year}
          picking={rematch.isPending}
          error={rematch.isError ? rematch.error.message : null}
          onPick={(hit) => rematch.mutate(hit)}
          onClose={() => setRematching(false)}
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
                  preview.data.problems.length === 0 && (preview.data.write || preview.data.changes),
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
    </Stack>
  );
}
