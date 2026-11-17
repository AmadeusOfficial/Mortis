import { Accordion, ActionIcon, Badge, Group, Image, Paper, Table, Text } from "@mantine/core";
import { useNavigate } from "react-router";
import type { SeasonInfo } from "../api";
import { IconPlay } from "../icons";

// 季标题行：有海报才显示小缩略图。
function SeasonLabel({ season }: { season: SeasonInfo }) {
  return (
    <Group gap="sm" wrap="nowrap">
      {season.poster_url !== null && (
        <Image src={season.poster_url} alt={season.title} h={48} w={32} fit="cover" radius="sm" />
      )}
      <Text size="sm">
        {season.title} · {season.episode_count} 集 · 有文件 {season.file_count}
      </Text>
    </Group>
  );
}

export function SeasonList({
  seasons,
  onPlay,
  playingId,
}: {
  seasons: SeasonInfo[];
  onPlay: (episodeId: number, title: string) => void;
  playingId: number | null;
}) {
  const navigate = useNavigate();
  return (
    <Accordion multiple variant="separated">
      {seasons.map((season) => (
        <Accordion.Item key={season.id} value={String(season.id)}>
          <Accordion.Control>
            <SeasonLabel season={season} />
          </Accordion.Control>
          <Accordion.Panel>
            <Paper>
              <Table verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={60}>集号</Table.Th>
                    <Table.Th w={120}>缩略图</Table.Th>
                    <Table.Th>标题</Table.Th>
                    <Table.Th w={120}>播出日期</Table.Th>
                    <Table.Th w={56}>
                      <span className="mo-sr-only">播放</span>
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {season.episodes.map((episode) => (
                    <Table.Tr
                      key={episode.id}
                      c={episode.has_file ? undefined : "dimmed"}
                      style={{ cursor: "pointer" }}
                      onClick={() => void navigate(`/items/${episode.id}`)}
                    >
                      <Table.Td>{episode.number ?? "—"}</Table.Td>
                      <Table.Td>
                        {episode.still_url !== null && (
                          <Image
                            src={episode.still_url}
                            alt={episode.title}
                            w={120}
                            fit="cover"
                            radius="sm"
                          />
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="nowrap">
                          <Text size="sm">{episode.title}</Text>
                          {!episode.has_file && (
                            <Badge size="xs" variant="light" color="gray">
                              缺
                            </Badge>
                          )}
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" c="dimmed">
                          {episode.air_date ?? "—"}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {episode.playable && (
                          <ActionIcon
                            variant="subtle"
                            aria-label={`播放第 ${episode.number ?? "?"} 集 ${episode.title}`}
                            loading={playingId === episode.id}
                            onClick={(event) => {
                              event.stopPropagation();
                              onPlay(episode.id, `${season.title} 第 ${episode.number ?? "?"} 集`);
                            }}
                          >
                            <IconPlay size={16} />
                          </ActionIcon>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion>
  );
}
