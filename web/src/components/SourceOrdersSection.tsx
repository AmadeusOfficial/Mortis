import {
  Accordion,
  Alert,
  Button,
  Group,
  Modal,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  type DataType,
  type OrderEntry,
  type SourceOrders,
  type SourceRef,
  fetchDataTypes,
  fetchSourceOrders,
  resetSourceOrders,
  saveSourceOrders,
} from "../api";
import styles from "../pages/SettingsPage.module.css";
import { IconDrag } from "../icons";
import { SettingCard } from "./settings/SettingLayout";
import { Pill } from "./ui";

const BUILTIN_TYPES_WITH_ORDERS = ["movie", "tv", "anime"];

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

export function SourceOrdersSection({ dataTypeId }: { dataTypeId?: string }) {
  const dataTypes = useQuery({ queryKey: ["data-types"], queryFn: fetchDataTypes });
  // 数据类型页只渲染自己那一份；不给参数时列出所有有来源顺序的类型。
  const orderTypes = (dataTypes.data ?? []).filter(
    (type) =>
      (type.plugin_id !== null || BUILTIN_TYPES_WITH_ORDERS.includes(type.id)) &&
      (dataTypeId === undefined || type.id === dataTypeId),
  );

  if (dataTypes.isError) {
    return (
      <SettingCard title="来源顺序">
        <div className={styles.cardBody}>
          <Alert color="red" title="读不到数据类型">
            {dataTypes.error.message}
          </Alert>
        </div>
      </SettingCard>
    );
  }
  if (!dataTypes.isSuccess) {
    return null;
  }
  if (orderTypes.length === 0) {
    // 指定了类型时它自己没有来源顺序，整块不显示。
    if (dataTypeId !== undefined) {
      return null;
    }
    return (
      <SettingCard
        title="来源顺序"
        description="还没有可以排来源的数据类型。"
      >
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            暂无可配置的数据类型。
          </Text>
        </div>
      </SettingCard>
    );
  }
  return (
    <>
      {orderTypes.map((type) => (
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

  const description = "每个字段按来源顺序取第一个有值的来源；整体顺序用于所有没单独设置的字段。";

  if (orders.isError) {
    return (
      <SettingCard title={`${dataType.name} 来源顺序`} description={description}>
        <div className={styles.cardBody}>
          <Alert color="red" title="读不到来源顺序">
            {orders.error.message}
          </Alert>
        </div>
      </SettingCard>
    );
  }
  if (draft === null || orders.data === undefined) {
    return (
      <SettingCard title={`${dataType.name} 来源顺序`} description={description}>
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            正在读取{dataType.name}的来源顺序…
          </Text>
        </div>
      </SettingCard>
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
    <SettingCard title={`${dataType.name} 来源顺序`} description={description}>
      <div className={styles.orderSectionLabel}>整体顺序</div>
      <OrderList
        entries={draft.whole}
        sources={sources}
        onChange={(index, delta) => setWhole(moveEntry(draft.whole, index, delta))}
      />
      {draft.fields.length > 0 && (
        <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
          <Accordion multiple variant="separated" chevronPosition="left">
            {draft.fields.map((field) => (
              <Accordion.Item
                key={field.key}
                value={field.key}
                style={{
                  background: "var(--mo-surface-2)",
                  borderColor: "var(--mo-line)",
                }}
              >
                <Accordion.Control>
                  <Group gap="xs">
                    <Text size="sm" fw={500}>
                      {field.label}
                    </Text>
                    <Pill tone={field.custom ? "accent" : "neutral"}>
                      {field.custom ? "单独设置" : "跟随整体顺序"}
                    </Pill>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="sm">
                    {field.custom ? (
                      <>
                        <div
                          style={{
                            border: "1px solid var(--mo-line)",
                            borderRadius: 8,
                            overflow: "hidden",
                            background: "var(--mo-surface)",
                          }}
                        >
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
                        </div>
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
                      <Group justify="space-between" wrap="wrap" gap="sm">
                        <Text size="sm" c="var(--mo-dim)">
                          这个字段跟着上面的整体顺序取来源。
                        </Text>
                        <Button
                          size="compact-sm"
                          variant="default"
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
        </div>
      )}
      <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
        <Stack gap="sm">
          {save.isError && (
            <Alert color="red" title="保存失败">
              {save.error.message}
            </Alert>
          )}
          <Group>
            <Button variant="filled" loading={save.isPending} onClick={() => save.mutate(draft)}>
              保存
            </Button>
            <Button variant="default" onClick={() => setConfirmReset(true)}>
              恢复默认
            </Button>
          </Group>
        </Stack>
      </div>
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
                variant="filled"
                loading={reset.isPending}
                onClick={() => reset.mutate(undefined, { onSuccess: () => setConfirmReset(false) })}
              >
                恢复默认
              </Button>
            </Group>
          </Stack>
        </Modal>
      )}
    </SettingCard>
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
  const refOf = (id: string) => sources.find((source) => source.id === id);
  const nameOf = (id: string) => refOf(id)?.name ?? id;

  if (entries.length === 0) {
    return (
      <div className={styles.cardBody}>
        <Text size="sm" c="var(--mo-dim)">
          还没有启用的来源。
        </Text>
      </div>
    );
  }

  return (
    <div>
      {entries.map((entry, index) => {
        const ref = refOf(entry.source);
        const unavailable = ref !== undefined && ref.available === false;
        return (
          <div
            key={entry.source}
            className={styles.orderRow}
            data-excluded={entry.excluded ? "true" : "false"}
            data-unavailable={unavailable ? "true" : "false"}
          >
            <span className={styles.orderGrip} aria-hidden="true">
              <IconDrag size={14} />
            </span>
            <span className={styles.orderRank}>{index + 1}</span>
            <Group gap="xs" style={{ flex: 1, minWidth: 0 }}>
              <Text size="sm" fw={500}>
                {nameOf(entry.source)}
              </Text>
              {ref === undefined && <Pill tone="neutral">未安装</Pill>}
              {unavailable && (
                <Text size="xs" c="var(--mo-dim)">
                  不可用：{ref.reason ?? "未知原因"}
                </Text>
              )}
            </Group>
            <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
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
          </div>
        );
      })}
    </div>
  );
}
