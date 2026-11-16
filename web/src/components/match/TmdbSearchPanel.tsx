import { Alert, Button, Group, Loader, NumberInput, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type MovieHit, type SearchKind, searchTmdbMovies } from "../../api";
import { IconSearch } from "../../icons";
import { MatchCandidate } from "./MatchCandidate";
import classes from "./MatchPanel.module.css";

type ScoredMovieHit = MovieHit & { score?: number | null };

export function TmdbSearchPanel({
  kind = "movie",
  initialQuery,
  initialYear,
  matchThreshold = 0.75,
  picking,
  error,
  embedded = false,
  autoFocus = false,
  onPick,
}: {
  kind?: SearchKind;
  initialQuery: string;
  initialYear: number | null;
  matchThreshold?: number;
  picking: boolean;
  error: string | null;
  embedded?: boolean;
  autoFocus?: boolean;
  onPick: (hit: MovieHit) => void;
}) {
  const [text, setText] = useState(initialQuery);
  const [year, setYear] = useState<number | "">(initialYear ?? "");
  const [submitted, setSubmitted] = useState<{ query: string; year: number | null }>({
    query: initialQuery,
    year: initialYear,
  });
  const [picked, setPicked] = useState<number | null>(null);
  const results = useQuery({
    queryKey: ["tmdb-search", kind, submitted.query, submitted.year],
    queryFn: () => searchTmdbMovies(submitted.query, submitted.year, kind),
    enabled: submitted.query.trim() !== "",
  });

  const titleLabel = kind === "tv" ? "剧名" : "片名";

  return (
    <div>
      <div className={embedded ? classes.headEmbedded : classes.headModal}>
        <form
          className={classes.searchForm}
          onSubmit={(event) => {
            event.preventDefault();
            setPicked(null);
            setSubmitted({ query: text, year: year === "" ? null : year });
          }}
        >
          <TextInput
            aria-label={titleLabel}
            placeholder={`输入${titleLabel}`}
            leftSection={<IconSearch size={14} />}
            value={text}
            style={{ flex: 1, minWidth: 0 }}
            data-autofocus={autoFocus || undefined}
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <NumberInput
            aria-label="年份"
            placeholder="年份"
            value={year}
            w={82}
            min={1870}
            max={2100}
            allowDecimal={false}
            hideControls
            onChange={(value) => setYear(typeof value === "number" ? value : "")}
          />
          <Button type="submit" variant="filled">
            搜索
          </Button>
        </form>
      </div>

      <div className={embedded ? classes.bodyEmbedded : classes.bodyModal}>
        {error !== null && (
          <div className={embedded ? classes.statusPadEmbedded : classes.statusPadModal}>
            <Alert color="red" title="没能选定">
              {error}
            </Alert>
          </div>
        )}
        {results.isFetching && (
          <Group
            justify="center"
            className={embedded ? classes.statusPadEmbedded : classes.statusPadModal}
          >
            <Loader size="sm" />
          </Group>
        )}
        {results.isError && (
          <div className={embedded ? classes.statusPadEmbedded : classes.statusPadModal}>
            <Alert color="red" title="搜索失败">
              {results.error.message}
            </Alert>
          </div>
        )}
        {results.isSuccess && results.data.length === 0 && (
          <div className={embedded ? classes.emptyTextEmbedded : classes.emptyTextModal}>
            TMDB 上没有找到。
          </div>
        )}
        {results.data !== undefined && results.data.length > 0 && (
          <div className={classes.list}>
            {results.data.map((hit, index) => {
              const scored = hit as ScoredMovieHit;
              return (
                <MatchCandidate
                  key={hit.tmdb_id}
                  title={hit.title}
                  originalTitle={hit.original_title}
                  year={hit.year}
                  source="tmdb"
                  overview={hit.overview}
                  posterUrl={hit.poster_url}
                  score={scored.score}
                  matchThreshold={matchThreshold}
                  isFirst={index === 0}
                  embedded={embedded}
                  loading={picking && picked === hit.tmdb_id}
                  disabled={picking && picked !== hit.tmdb_id}
                  onPick={() => {
                    setPicked(hit.tmdb_id);
                    onPick(hit);
                  }}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
