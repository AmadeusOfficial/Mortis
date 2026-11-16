import { Alert, Button, Group, Loader, Select, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type MovieHit,
  type UnmatchedFile,
  bindMediaFile,
  fetchLibraries,
  fetchUnmatched,
} from "../api";
import { TmdbSearchModal } from "../components/TmdbSearchModal";
import { formatSize } from "../format";

function splitPath(path: string): [string, string] {
  const index = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return [path.slice(0, index), path.slice(index + 1)];
}

export function UnmatchedPage() {
  const queryClient = useQueryClient();
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const [libraryId, setLibraryId] = useState<string | null>(null);
  const selected = libraryId === null ? null : Number(libraryId);
  const files = useQuery({
    queryKey: ["unmatched", selected],
    queryFn: () => fetchUnmatched(selected),
  });
  const [binding, setBinding] = useState<UnmatchedFile | null>(null);
  const bind = useMutation({
    mutationFn: ({ file, hit }: { file: UnmatchedFile; hit: MovieHit }) =>
      bindMediaFile(file.id, hit),
    onSuccess: (_result, { file, hit }) => {
      setBinding(null);
      void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      void queryClient.invalidateQueries({ queryKey: ["wall", file.library_id] });
      notifications.show({ color: "green", message: `已匹配为《${hit.title}》，正在刮削` });
    },
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>未识别</Title>
        <Select
          placeholder="全部媒体库"
          clearable
          w={220}
          value={libraryId}
          onChange={setLibraryId}
          data={(libraries.data ?? []).map((library) => ({
            value: String(library.id),
            label: library.name,
          }))}
          aria-label="媒体库"
        />
      </Group>
      <Text size="sm" c="dimmed">
        扫描时没能自动认出的视频。点「匹配」在 TMDB
        上搜索并选定一部，同目录的分段和字幕会一起归到这部电影。
      </Text>
      {files.isPending && <Loader size="sm" />}
      {files.isError && (
        <Alert color="red" title="读不到文件">
          {files.error.message}
        </Alert>
      )}
      {files.isSuccess && files.data.length === 0 && (
        <Text c="dimmed">没有未识别的文件。</Text>
      )}
      {files.isSuccess && files.data.length > 0 && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>文件</Table.Th>
              <Table.Th>猜测</Table.Th>
              <Table.Th>媒体库</Table.Th>
              <Table.Th>大小</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {files.data.map((file) => {
              const [folder, name] = splitPath(file.path);
              return (
                <Table.Tr key={file.id}>
                  <Table.Td>
                    <Text size="sm" ff="monospace">
                      {name}
                      {file.data_type_id === "tv" && ` · ${file.video_count} 个视频`}
                    </Text>
                    <Text size="xs" c="dimmed" ff="monospace">
                      {folder}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {file.guess_title}
                      {file.guess_year !== null && `（${file.guess_year}）`}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{file.library_name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{formatSize(file.size)}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="xs"
                      variant="light"
                      aria-label={`匹配${name}`}
                      onClick={() => {
                        bind.reset();
                        setBinding(file);
                      }}
                    >
                      匹配
                    </Button>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
      {binding !== null && (
        <TmdbSearchModal
          title={`匹配 ${splitPath(binding.path)[1]}`}
          kind={binding.data_type_id === "tv" ? "tv" : "movie"}
          initialQuery={binding.guess_title}
          initialYear={binding.guess_year}
          picking={bind.isPending}
          error={bind.isError ? bind.error.message : null}
          onPick={(hit) => bind.mutate({ file: binding, hit })}
          onClose={() => setBinding(null)}
        />
      )}
    </Stack>
  );
}
