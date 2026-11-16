import { Group, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "bad";

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className="mo-pill" data-tone={tone}>
      {children}
    </span>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  tmdb: "TMDB",
  imdb: "IMDb",
  tvdb: "TVDB",
  fanarttv: "Fanart",
  bangumi: "Bangumi",
  vndb: "VNDB",
  cngal: "CnGal",
  ymgal: "月幕",
  manual: "手改",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

export function SourceBadge({ source, label }: { source: string; label?: string }) {
  if (source === "manual") {
    return (
      <span className="mo-pill" data-tone="warn" style={{ height: 18, fontSize: 10.5, borderRadius: 4 }}>
        手改
      </span>
    );
  }
  return (
    <span className="mo-pill" data-tone="source">
      {label ?? sourceLabel(source)}
    </span>
  );
}

export function FilterChip({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count?: number;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button type="button" className="mo-filter" data-active={active} onClick={onClick}>
      {children}
      {count !== undefined && <span className="mo-mono">{count}</span>}
    </button>
  );
}

export function PageHeader({
  title,
  pills,
  description,
  actions,
}: {
  title: ReactNode;
  pills?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" mb="lg">
      <Stack gap={6} miw={0}>
        <Group gap="sm" wrap="wrap">
          <Title order={2} fz={26} lh={1.15}>
            {title}
          </Title>
          {pills}
        </Group>
        {description && (
          <Text size="sm" c="var(--mo-dim)">
            {description}
          </Text>
        )}
      </Stack>
      {actions && (
        <Group gap="xs" wrap="nowrap">
          {actions}
        </Group>
      )}
    </Group>
  );
}
