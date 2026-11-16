import { Alert, Button, Group, Loader, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type SourceHit, fetchSources, searchSource } from "../../api";
import { IconSearch } from "../../icons";
import { FilterChip } from "../ui";
import { MatchCandidate } from "./MatchCandidate";
import classes from "./MatchPanel.module.css";

type ScoredSourceHit = SourceHit & { score?: number | null };

// 插件数据类型的匹配：用 FilterChip 选来源，再在这个来源里按名字搜。
export function SourceSearchPanel({
  dataType,
  initialQuery,
  exclude = [],
  matchThreshold = 0.75,
  picking,
  error,
  embedded = false,
  autoFocus = false,
  onPick,
}: {
  dataType: string;
  initialQuery: string;
  // 不列出的来源 id，比如补充来源时的主来源。
  exclude?: string[];
  matchThreshold?: number;
  picking: boolean;
  error: string | null;
  embedded?: boolean;
  autoFocus?: boolean;
  onPick: (source: string, hit: SourceHit) => void;
}) {
  const sourcesQuery = useQuery({
    queryKey: ["sources", dataType],
    queryFn: () => fetchSources(dataType),
  });
  const choices = (sourcesQuery.data ?? []).filter((entry) => !exclude.includes(entry.id));
  const [chosen, setChosen] = useState<string | null>(null);
  const source = chosen ?? choices[0]?.id ?? null;
  const [text, setText] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [picked, setPicked] = useState<string | null>(null);
  const results = useQuery({
    queryKey: ["source-search", source, query],
    queryFn: () => searchSource(source ?? "", query),
    enabled: source !== null && query.trim() !== "",
  });
  const sourceName = choices.find((entry) => entry.id === source)?.name ?? "";

  return (
    <div>
      <div className={embedded ? classes.headEmbedded : classes.headModal}>
        <form
          className={classes.searchForm}
          onSubmit={(event) => {
            event.preventDefault();
            setPicked(null);
            setQuery(text);
          }}
        >
          <TextInput
            aria-label="名称"
            placeholder="输入名称搜索"
            leftSection={<IconSearch size={14} />}
            value={text}
            style={{ flex: 1, minWidth: 0 }}
            data-autofocus={autoFocus || undefined}
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <Button type="submit" variant="filled">
            搜索
          </Button>
        </form>

        {choices.length > 0 && (
          <div className={classes.sourceChips} role="group" aria-label="来源">
            {choices.map((entry) => (
              <FilterChip
                key={entry.id}
                active={entry.id === source}
                onClick={() => {
                  setChosen(entry.id);
                  setPicked(null);
                }}
              >
                {entry.name}
              </FilterChip>
            ))}
          </div>
        )}
      </div>

      <div className={embedded ? classes.bodyEmbedded : classes.bodyModal}>
        {sourcesQuery.isSuccess && choices.length === 0 && (
          <div className={embedded ? classes.statusPadEmbedded : classes.statusPadModal}>
            <Alert color="yellow" title="没有可用的来源">
              到设置页的「插件」里启用至少一个来源。
            </Alert>
          </div>
        )}
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
            {sourceName} 上没有找到。
          </div>
        )}
        {results.data !== undefined && results.data.length > 0 && source !== null && (
          <div className={classes.list}>
            {results.data.map((hit, index) => {
              const scored = hit as ScoredSourceHit;
              return (
                <MatchCandidate
                  key={hit.external_id}
                  title={hit.title}
                  originalTitle={hit.original_title}
                  year={hit.year}
                  source={source}
                  overview={hit.overview}
                  posterUrl={hit.poster_url}
                  score={scored.score}
                  matchThreshold={matchThreshold}
                  isFirst={index === 0}
                  embedded={embedded}
                  loading={picking && picked === hit.external_id}
                  disabled={picking && picked !== hit.external_id}
                  pickAriaLabel={`选${hit.title}`}
                  onPick={() => {
                    setPicked(hit.external_id);
                    onPick(source, hit);
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
