import {
  Alert,
  Group,
  Loader,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router";
import { type SortOrder, type WallSort, fetchLibrary, fetchWall } from "../api";
import { PosterCard } from "../components/PosterCard";
import { LibraryActions } from "../libraryActions";

const SORTS = [
  { value: "title:asc", label: "片名" },
  { value: "release_date:desc", label: "上映日期（新到旧）" },
  { value: "release_date:asc", label: "上映日期（旧到新）" },
  { value: "created_at:desc", label: "最近加入" },
];

export function LibraryWallPage() {
  const id = Number(useParams().libraryId);
  const library = useQuery({ queryKey: ["library", id], queryFn: () => fetchLibrary(id) });
  const [search, setSearch] = useState("");
  const [query] = useDebouncedValue(search, 300);
  const [sortKey, setSortKey] = useState("title:asc");
  const [sort, order] = sortKey.split(":") as [WallSort, SortOrder];
  const wall = useQuery({
    queryKey: ["wall", id, query, sort, order],
    queryFn: () => fetchWall(id, query, sort, order),
  });

  if (library.isError) {
    return (
      <Alert color="red" title="读不到媒体库">
        {library.error.message}
      </Alert>
    );
  }
  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="xs" align="baseline">
          <Title order={2}>{library.data?.name ?? "媒体库"}</Title>
          {wall.isSuccess && <Text c="dimmed">{wall.data.length} 部</Text>}
        </Group>
        {library.isSuccess && <LibraryActions library={library.data} />}
      </Group>
      <Group>
        <TextInput
          placeholder="搜索片名"
          aria-label="搜索片名"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          w={260}
        />
        <Select
          data={SORTS}
          value={sortKey}
          onChange={(value) => setSortKey(value ?? "title:asc")}
          allowDeselect={false}
          w={200}
          aria-label="排序"
        />
      </Group>
      {wall.isPending && <Loader size="sm" />}
      {wall.isError && (
        <Alert color="red" title="读不到电影">
          {wall.error.message}
        </Alert>
      )}
      {wall.isSuccess && wall.data.length === 0 && (
        <Text c="dimmed">
          {query === "" ? "库里还没有电影。点「扫描」找出目录里的电影。" : "没有找到。"}
        </Text>
      )}
      {wall.isSuccess && wall.data.length > 0 && (
        <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }} spacing="md">
          {wall.data.map((item) => (
            <PosterCard key={item.id} item={item} />
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}
