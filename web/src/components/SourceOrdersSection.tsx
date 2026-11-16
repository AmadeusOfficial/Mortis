import {
  Accordion,
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Paper,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  type DataType,
  type OrderEntry,
  type SourceRef,
  type SourceOrders,
  fetchDataTypes,
  fetchSourceOrders,
  resetSourceOrders,
  saveSourceOrders,
} from "../api";

// 一份可编辑的顺序：整体顺序和字段自己的顺序用的是同一种结构。
type FieldDraft = {
  key: string;
  label: string;
  custom: boolean;
  order: OrderEntry[];
};

type Draft = { whole: OrderEntry[]; fields: FieldDraft[] };

function toDraft(orders: SourceOrders): Draft {
  return {
    whole: orders.whole.map((entry) => ({ ...entry })),
    fields: orders.fields.map((field) => ({
      key: field.key,
      label: field.label,
      custom: field.custom,
      order: field.order.map((entry) => ({ ...entry })),
    })),
  };
}

// 返回值里的字段顺序是后端重排过的，保存后用整份返回值覆盖本地状态。
function moveEntry(entries: OrderEntry[], index: number, delta: number): OrderEntry[] {
  const target = index + delta;
  if (target < 0 || target >= entries.length) {
    return entries;
  }
  const next = [...entries];
  const [entry] = next.splice(index, 1);
  next.splice(target, 0, entry);
  return next;
}

export function SourceOrdersSection() {
  const dataTypes = useQuery({ queryKey: ["data-types"], queryFn: fetchDataTypes });
  const pluginTypes = (dataTypes.data ?? []).filter((type) => type.plugin_id !== null);

  if (dataTypes.isError) {
    return (
      <Paper withBorder p="md">
        <Stack>
          <Title order={4}>来源顺序</Title>
          <Alert color="red" title="读不到数据类型">
            {dataTypes.error.message}
          </Alert>
        </Stack>
      </Paper>
    );
  }
  if (!dataTypes.isSuccess) {
    return null;
  }
  if (pluginTypes.length === 0) {
    return (
      <Paper withBorder p="md">
        <Stack>
          <Title order={4}>来源顺序</Title>
          <Text size="sm" c="dimmed">
            还没有插件数据类型。启用数据类型插件后，这里会按类型列出各自的来源顺序。
          </Text>
        </Stack>
      </Paper>
    );
  }
  return (
    <>
      <Text size="sm" c="dimmed">
        每个字段按来源顺序取第一个有值的来源；整体顺序用于所有没单独设置的字段。
      </Text>
      {pluginTypes.map((type) => (
        <SourceOrderBlock key={type.id} dataType={type} />
      ))}
    </>
  );
}

function SourceOrderBlock({ dataType }: { dataType: DataType }) {
  const queryClient = useQueryClient();
  const orders = useQuery({
    queryKey: ["source-orders", dataType.id],
    queryFn: () => fetchSourceOrders(dataType.id),
    // 切回窗口时重新拉取会把正在编辑的顺序冲掉，编辑期间只认保存和恢复的返回值。
    refetchOnWindowFocus: false,
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  useEffect(() => {
    if (orders.data !== undefined) {
      setDraft(toDraft(orders.data));
    }
  }, [orders.data]);

  const apply = (saved: SourceOrders) => {
    queryClient.setQueryData(["source-orders", dataType.id], saved);
    setDraft(toDraft(saved));
  };

  const save = useMutation({
    mutationFn: (value: Draft) => {
      const body: Record<string, OrderEntry[] | null> = {
        "": value.whole.map((entry) => ({ ...entry, excluded: false })),
      };
      for (const field of value.fields) {
        body[field.key] = field.custom
          ? field.order.map((entry) => ({ source: entry.source, excluded: entry.excluded }))
          : null;
      }
      return saveSourceOrders(dataType.id, body);
    },
    onSuccess: (saved) => {
      apply(saved);
      notifications.show({ color: "green", message: `${dataType.name}的来源顺序已保存` });
    },
  });

  const reset = useMutation({
    mutationFn: () => resetSourceOrders(dataType.id),
    onSuccess: (saved) => {
      apply(saved);
      notifications.show({ color: "green", message: `${dataType.name}的来源顺序已恢复默认` });
    },
  });

  if (orders.isError) {
    return (
      <Paper withBorder p="md">
        <Stack>
          <Title order={4}>{dataType.name} 来源顺序</Title>
          <Alert color="red" title="读不到来源顺序">
            {orders.error.message}
          </Alert>
        </Stack>
      </Paper>
    );
  }
  if (draft === null || orders.data === undefined) {
    return (
      <Paper withBorder p="md">
        <Text c="dimmed">正在读取{dataType.name}的来源顺序…</Text>
      </Paper>
    );
  }

  const sources: SourceRef[] = orders.data.sources;
  const setWhole = (whole: OrderEntry[]) => setDraft({ ...draft, whole });
  const setField = (key: string, patch: Partial<FieldDraft>) =>
    setDraft({
      ...draft,
      fields: draft.fields.map((field) => (field.key === key ? { ...field, ...patch } : field)),
    });

  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>{dataType.name} 来源顺序</Title>
        <Text size="sm" fw={500}>
          整体顺序
        </Text>
        <OrderList
          entries={draft.whole}
          sources={sources}
          onChange={(index, delta) => setWhole(moveEntry(draft.whole, index, delta))}
        />
        {draft.fields.length > 0 && (
          <Accordion multiple variant="separated" chevronPosition="left">
            {draft.fields.map((field) => (
              <Accordion.Item key={field.key} value={field.key}>
                <Accordion.Control>
                  <Group gap="xs">
                    <Text size="sm">{field.label}</Text>
                    <Badge size="xs" variant="light" color={field.custom ? "blue" : "gray"}>
                      {field.custom ? "单独设置" : "跟随整体顺序"}
                    </Badge>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="sm">
                    {field.custom ? (
                      <>
                        <OrderList
                          entries={field.order}
                          sources={sources}
                          showExcluded
                          onChange={(index, delta) =>
                            setField(field.key, {
                              order: moveEntry(field.order, index, delta),
                            })
                          }
                          onToggleExcluded={(index) =>
                            setField(field.key, {
                              order: field.order.map((entry, at) =>
                                at === index ? { ...entry, excluded: !entry.excluded } : entry,
                              ),
                            })
                          }
                        />
                        <Group>
                          <Button
                            size="compact-sm"
                            variant="default"
                            onClick={() => setField(field.key, { custom: false })}
                          >
                            改回跟随整体
                          </Button>
                        </Group>
                      </>
                    ) : (
                      <Group>
                        <Text size="sm" c="dimmed">
                          这个字段跟着上面的整体顺序取来源。
                        </Text>
                        <Button
                          size="compact-sm"
                          variant="light"
                          onClick={() =>
                            setField(field.key, {
                              custom: true,
                              // 从当前整体顺序开始改，用户刚调过的顺序不会丢。
                              order: draft.whole.map((entry) => ({
                                source: entry.source,
                                excluded: false,
                              })),
                            })
                          }
                        >
                          单独设置
                        </Button>
                      </Group>
                    )}
                  </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
        )}
        {save.isError && (
          <Alert color="red" title="保存失败">
            {save.error.message}
          </Alert>
        )}
        <Group>
          <Button loading={save.isPending} onClick={() => save.mutate(draft)}>
            保存
          </Button>
          <Button variant="default" onClick={() => setConfirmReset(true)}>
            恢复默认
          </Button>
        </Group>
      </Stack>
      {confirmReset && (
        <Modal opened onClose={() => setConfirmReset(false)} title="恢复默认来源顺序">
          <Stack>
            <Text size="sm">
              把{dataType.name}的来源顺序恢复默认？现在单独设置的字段会一起重置。
            </Text>
            {reset.isError && (
              <Alert color="red" title="恢复失败">
                {reset.error.message}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setConfirmReset(false)}>
                取消
              </Button>
              <Button
                loading={reset.isPending}
                onClick={() => reset.mutate(undefined, { onSuccess: () => setConfirmReset(false) })}
              >
                恢复默认
              </Button>
            </Group>
          </Stack>
        </Modal>
      )}
    </Paper>
  );
}

function OrderList({
  entries,
  sources,
  showExcluded = false,
  onChange,
  onToggleExcluded,
}: {
  entries: OrderEntry[];
  sources: SourceRef[];
  showExcluded?: boolean;
  onChange: (index: number, delta: number) => void;
  onToggleExcluded?: (index: number) => void;
}) {
  const nameOf = (id: string) => sources.find((source) => source.id === id)?.name ?? id;
  const installed = (id: string) => sources.some((source) => source.id === id);

  if (entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        还没有启用的来源。
      </Text>
    );
  }

  return (
    <Table verticalSpacing={4}>
      <Table.Tbody>
        {entries.map((entry, index) => (
          <Table.Tr key={entry.source} c={entry.excluded ? "dimmed" : undefined}>
            <Table.Td w={40}>{index + 1}</Table.Td>
            <Table.Td>
              <Group gap="xs">
                <Text size="sm">{nameOf(entry.source)}</Text>
                {!installed(entry.source) && (
                  <Badge size="xs" variant="light" color="gray">
                    未安装
                  </Badge>
                )}
              </Group>
            </Table.Td>
            <Table.Td w={320}>
              <Group gap={4} wrap="nowrap" justify="flex-end">
                <Button
                  size="compact-xs"
                  variant="subtle"
                  aria-label={`上移${nameOf(entry.source)}`}
                  disabled={index === 0}
                  onClick={() => onChange(index, -1)}
                >
                  上移
                </Button>
                <Button
                  size="compact-xs"
                  variant="subtle"
                  aria-label={`下移${nameOf(entry.source)}`}
                  disabled={index === entries.length - 1}
                  onClick={() => onChange(index, 1)}
                >
                  下移
                </Button>
                {showExcluded && onToggleExcluded !== undefined && (
                  <Switch
                    size="xs"
                    label="排除"
                    checked={entry.excluded}
                    onChange={() => onToggleExcluded(index)}
                  />
                )}
              </Group>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}
