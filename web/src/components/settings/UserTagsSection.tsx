import { Alert, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type UserTag,
  deleteUserTag,
  fetchDeleteTagImpact,
  fetchMergeImpact,
  fetchUserTags,
  mergeUserTags,
  renameUserTag,
} from "../../api";
import styles from "../../pages/SettingsPage.module.css";
import { SettingCard } from "./SettingLayout";

type Pending = { kind: "rename" | "merge" | "delete"; tag: UserTag };

// 这个类型的自定义标签：改名、合并到另一个、删除。合并和删除先算出会影响几部作品再确认。
export function UserTagsSection({ dataTypeId }: { dataTypeId: string }) {
  const queryClient = useQueryClient();
  const tags = useQuery({
    queryKey: ["user-tags", dataTypeId],
    queryFn: () => fetchUserTags(dataTypeId),
  });
  const [pending, setPending] = useState<Pending | null>(null);
  const done = (message: string) => {
    setPending(null);
    void queryClient.invalidateQueries({ queryKey: ["user-tags", dataTypeId] });
    void queryClient.invalidateQueries({ queryKey: ["item"] });
    notifications.show({ color: "green", message });
  };

  return (
    <SettingCard title="标签" description="详情页里给作品打的「我的标签」，只在这个类型里通用。">
      {tags.isError && (
        <div className={styles.cardBody}>
          <Alert color="red" title="读不到标签">
            {tags.error.message}
          </Alert>
        </div>
      )}
      {tags.isSuccess && tags.data.length === 0 && (
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            还没有标签，在作品详情页的「我的标签」里添加。
          </Text>
        </div>
      )}
      {tags.data?.map((tag) => (
        <div key={tag.id} className={styles.listRow}>
          <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
            <Text size="sm" fw={600} truncate>
              {tag.name}
            </Text>
            <Text size="xs" c="var(--mo-dim)" style={{ flexShrink: 0 }}>
              {tag.count} 部作品
            </Text>
          </Group>
          <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
            <Button size="xs" variant="subtle" onClick={() => setPending({ kind: "rename", tag })}>
              改名
            </Button>
            <Button
              size="xs"
              variant="subtle"
              disabled={tags.data.length < 2}
              onClick={() => setPending({ kind: "merge", tag })}
            >
              合并到…
            </Button>
            <Button
              size="xs"
              variant="subtle"
              color="red"
              onClick={() => setPending({ kind: "delete", tag })}
            >
              删除
            </Button>
          </Group>
        </div>
      ))}
      {pending?.kind === "rename" && (
        <RenameModal tag={pending.tag} onClose={() => setPending(null)} onDone={done} />
      )}
      {pending?.kind === "merge" && (
        <MergeModal
          tag={pending.tag}
          others={(tags.data ?? []).filter((tag) => tag.id !== pending.tag.id)}
          onClose={() => setPending(null)}
          onDone={done}
        />
      )}
      {pending?.kind === "delete" && (
        <DeleteModal tag={pending.tag} onClose={() => setPending(null)} onDone={done} />
      )}
    </SettingCard>
  );
}

function RenameModal({
  tag,
  onClose,
  onDone,
}: {
  tag: UserTag;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [name, setName] = useState(tag.name);
  const rename = useMutation({
    mutationFn: () => renameUserTag(tag.id, name),
    onSuccess: (saved) => onDone(`已改名为「${saved.name}」`),
  });
  return (
    <Modal opened onClose={onClose} title={`给「${tag.name}」改名`}>
      <Stack>
        <TextInput
          label="新名字"
          value={name}
          maxLength={40}
          data-autofocus
          onChange={(event) => setName(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              rename.mutate();
            }
          }}
        />
        {rename.isError && (
          <Alert color="red" title="没有改名">
            {rename.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button loading={rename.isPending} disabled={name.trim() === ""} onClick={() => rename.mutate()}>
            保存
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function MergeModal({
  tag,
  others,
  onClose,
  onDone,
}: {
  tag: UserTag;
  others: UserTag[];
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [targetId, setTargetId] = useState<number | null>(null);
  const impact = useQuery({
    queryKey: ["user-tag-merge-impact", tag.id, targetId],
    queryFn: () => fetchMergeImpact([tag.id], targetId ?? 0),
    enabled: targetId !== null,
    gcTime: 0,
  });
  const target = others.find((other) => other.id === targetId);
  const merge = useMutation({
    mutationFn: () => mergeUserTags([tag.id], targetId ?? 0, impact.data?.count ?? 0),
    onSuccess: (saved) => onDone(`已把「${tag.name}」并到「${saved.name}」`),
    onError: () => void impact.refetch(),
  });
  return (
    <Modal opened onClose={onClose} title={`把「${tag.name}」合并到…`}>
      <Stack>
        <Select
          label="并到哪个标签"
          placeholder="选一个标签"
          data={others.map((other) => ({ value: String(other.id), label: other.name }))}
          value={targetId === null ? null : String(targetId)}
          onChange={(value) => setTargetId(value === null ? null : Number(value))}
          searchable
          data-autofocus
        />
        {target !== undefined && impact.data !== undefined && (
          <Text size="sm">
            会影响 {impact.data.count} 部作品：它们的「{tag.name}」换成「{target.name}」，然后删掉「
            {tag.name}」。
          </Text>
        )}
        {impact.isError && (
          <Alert color="red" title="算不出影响">
            {impact.error.message}
          </Alert>
        )}
        {merge.isError && (
          <Alert color="red" title="没有合并">
            {merge.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            loading={merge.isPending}
            disabled={impact.data === undefined || impact.isFetching}
            onClick={() => merge.mutate()}
          >
            合并
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function DeleteModal({
  tag,
  onClose,
  onDone,
}: {
  tag: UserTag;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const impact = useQuery({
    queryKey: ["user-tag-delete-impact", tag.id],
    queryFn: () => fetchDeleteTagImpact(tag.id),
    gcTime: 0,
  });
  const remove = useMutation({
    mutationFn: () => deleteUserTag(tag.id, impact.data?.count ?? 0),
    onSuccess: () => onDone(`已删除标签「${tag.name}」`),
    onError: () => void impact.refetch(),
  });
  return (
    <Modal opened onClose={onClose} title={`删除标签「${tag.name}」`}>
      <Stack>
        {impact.data !== undefined && (
          <Text size="sm">
            会影响 {impact.data.count} 部作品：从它们身上去掉这个标签。作品本身不删。
          </Text>
        )}
        {impact.isError && (
          <Alert color="red" title="算不出影响">
            {impact.error.message}
          </Alert>
        )}
        {remove.isError && (
          <Alert color="red" title="没有删除">
            {remove.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            color="red"
            loading={remove.isPending}
            disabled={impact.data === undefined || impact.isFetching}
            onClick={() => remove.mutate()}
          >
            删除
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
