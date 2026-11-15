import {
  Alert,
  Button,
  Group,
  Image,
  Loader,
  Modal,
  NumberInput,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type MovieHit, searchTmdbMovies } from "../api";

export function TmdbSearchModal({
  title,
  initialQuery,
  initialYear,
  picking,
  error,
  onPick,
  onClose,
}: {
  title: string;
  initialQuery: string;
  initialYear: number | null;
  picking: boolean;
  error: string | null;
  onPick: (hit: MovieHit) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(initialQuery);
  const [year, setYear] = useState<number | "">(initialYear ?? "");
  const [submitted, setSubmitted] = useState<{ query: string; year: number | null }>({
    query: initialQuery,
    year: initialYear,
  });
  const [picked, setPicked] = useState<number | null>(null);
  const results = useQuery({
    queryKey: ["tmdb-search", submitted.query, submitted.year],
    queryFn: () => searchTmdbMovies(submitted.query, submitted.year),
    enabled: submitted.query.trim() !== "",
  });
  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setSubmitted({ query: text, year: year === "" ? null : year });
        }}
      >
        <Group align="flex-end">
          <TextInput
            label="片名"
            value={text}
            style={{ flex: 1 }}
            data-autofocus
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <NumberInput
            label="年份"
            value={year}
            w={110}
            min={1870}
            max={2100}
            allowDecimal={false}
            onChange={(value) => setYear(typeof value === "number" ? value : "")}
          />
          <Button type="submit">搜索</Button>
        </Group>
      </form>
      <Stack mt="md" gap="sm">
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
          <Text c="dimmed">TMDB 上没有找到。</Text>
        )}
        {results.data?.map((hit) => (
          <Group key={hit.tmdb_id} align="flex-start" wrap="nowrap">
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
              {hit.original_title !== hit.title && (
                <Text size="xs" c="dimmed">
                  {hit.original_title}
                </Text>
              )}
              <Text size="xs" lineClamp={2}>
                {hit.overview}
              </Text>
            </Stack>
            <Button
              size="xs"
              variant="light"
              loading={picking && picked === hit.tmdb_id}
              disabled={picking && picked !== hit.tmdb_id}
              onClick={() => {
                setPicked(hit.tmdb_id);
                onPick(hit);
              }}
            >
              选这部
            </Button>
          </Group>
        ))}
      </Stack>
    </Modal>
  );
}
