import {
  Alert,
  Button,
  Group,
  Image,
  Loader,
  Modal,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type SourceHit, fetchSources, searchSource } from "../api";

// 插件数据类型的匹配：先选来源，再在这个来源里按名字搜。
export function SourceSearchModal({
  title,
  dataType,
  initialQuery,
  picking,
  error,
  onPick,
  onClose,
}: {
  title: string;
  dataType: string;
  initialQuery: string;
  picking: boolean;
  error: string | null;
  onPick: (source: string, hit: SourceHit) => void;
  onClose: () => void;
}) {
  const sources = useQuery({
    queryKey: ["sources", dataType],
    queryFn: () => fetchSources(dataType),
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const source = chosen ?? sources.data?.[0]?.id ?? null;
  const [text, setText] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [picked, setPicked] = useState<string | null>(null);
  const results = useQuery({
    queryKey: ["source-search", source, query],
    queryFn: () => searchSource(source ?? "", query),
    enabled: source !== null && query.trim() !== "",
  });
  const sourceName = sources.data?.find((entry) => entry.id === source)?.name ?? "";
  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(text);
        }}
      >
        <Group align="flex-end">
          <Select
            label="来源"
            w={150}
            value={source}
            allowDeselect={false}
            data={(sources.data ?? []).map((entry) => ({ value: entry.id, label: entry.name }))}
            onChange={(value) => {
              setChosen(value);
              setPicked(null);
            }}
          />
          <TextInput
            label="名称"
            value={text}
            style={{ flex: 1 }}
            data-autofocus
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <Button type="submit">搜索</Button>
        </Group>
      </form>
      <Stack mt="md" gap="sm">
        {sources.isSuccess && sources.data.length === 0 && (
          <Alert color="yellow" title="没有可用的来源">
            到设置页的「插件」里启用至少一个来源。
          </Alert>
        )}
        {error !== null && (
          <Alert color="red" title="没能选定">
            {error}
          </Alert>
        )}
        {results.isFetching && <Loader size="sm" />}
        {results.isError && (
          <Alert color="red" title="搜索失败">
            {results.error.message}
          </Alert>
        )}
        {results.isSuccess && results.data.length === 0 && (
          <Text c="dimmed">{sourceName} 上没有找到。</Text>
        )}
        {results.data?.map((hit) => (
          <Group key={hit.external_id} align="flex-start" wrap="nowrap">
            {hit.poster_url !== null ? (
              <Image src={hit.poster_url} alt={hit.title} w={46} h={69} radius="sm" />
            ) : (
              <div style={{ width: 46, height: 69, background: "var(--mantine-color-gray-1)" }} />
            )}
            <Stack gap={2} style={{ flex: 1 }}>
              <Text fw={500}>
                {hit.title}
                {hit.year !== null && `（${hit.year}）`}
              </Text>
              {hit.original_title !== null && hit.original_title !== hit.title && (
                <Text size="xs" c="dimmed">
                  {hit.original_title}
                </Text>
              )}
              {hit.overview !== null && (
                <Text size="xs" lineClamp={2}>
                  {hit.overview}
                </Text>
              )}
            </Stack>
            <Button
              size="xs"
              variant="light"
              loading={picking && picked === hit.external_id}
              disabled={picking && picked !== hit.external_id}
              onClick={() => {
                if (source === null) {
                  return;
                }
                setPicked(hit.external_id);
                onPick(source, hit);
              }}
            >
              选这个
            </Button>
          </Group>
        ))}
      </Stack>
    </Modal>
  );
}
