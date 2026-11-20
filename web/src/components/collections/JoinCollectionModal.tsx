import {
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  Radio,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  type WallItem,
  assignCollection,
  fetchCollectionSuggestions,
  fetchCollections,
} from "../../api";

export function JoinCollectionModal({
  libraryId,
  selected,
  onClose,
  onJoined,
}: {
  libraryId: number;
  selected: WallItem[];
  onClose: () => void;
  onJoined: (result: { id: number; title: string }) => void;
}) {
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [title, setTitle] = useState("");
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [extra, setExtra] = useState<number[]>([]);
  const collections = useQuery({
    queryKey: ["collections", libraryId],
    queryFn: () => fetchCollections(libraryId),
  });
  const suggestions = useQuery({
    queryKey: ["collection-suggestions", libraryId, selected.map((item) => item.id).join(",")],
    queryFn: () =>
      fetchCollectionSuggestions(
        libraryId,
        selected.map((item) => item.id),
      ),
    enabled: mode === "new",
  });
  const existing = collections.data ?? [];
  const targetId = collectionId === null ? null : Number(collectionId);
  const moved = selected.filter(
    (item) =>
      item.collection_id != null && (mode === "new" || item.collection_id !== targetId),
  ).length;
  const workIds = [
    ...selected.map((item) => item.id),
    ...extra.filter((id) => selected.every((item) => item.id !== id)),
  ];
  const save = useMutation({
    mutationFn: () =>
      assignCollection(libraryId, {
        work_ids: workIds,
        ...(mode === "new" ? { title: title.trim() } : { collection_id: targetId ?? undefined }),
      }),
    onSuccess: (result) => onJoined(result),
  });
  const canSave = mode === "new" ? title.trim() !== "" : targetId !== null;

  return (
    <Modal opened onClose={onClose} title="加入合集" size="md">
      <Stack gap="sm">
        <Radio.Group value={mode} onChange={(value) => setMode(value as "new" | "existing")}>
          <Group>
            <Radio value="new" label="新建合集" />
            <Radio value="existing" label="已有合集" disabled={existing.length === 0} />
          </Group>
        </Radio.Group>
        {mode === "existing" && existing.length === 0 && (
          <Text size="sm" c="dimmed">
            还没有合集
          </Text>
        )}
        {mode === "new" ? (
          <TextInput
            label="合集名"
            value={title}
            onChange={(event) => setTitle(event.currentTarget.value)}
            data-autofocus
          />
        ) : (
          <Radio.Group value={collectionId} onChange={setCollectionId}>
            <Stack gap={6}>
              {existing.map((collection) => (
                <Radio key={collection.id} value={String(collection.id)} label={collection.title} />
              ))}
            </Stack>
          </Radio.Group>
        )}
        {mode === "new" && (suggestions.data ?? []).length > 0 && (
          <Stack gap={6}>
            <Text size="sm">来源里相关、这次没勾上的游戏。默认不收入。</Text>
            {(suggestions.data ?? []).map((item) => (
              <Checkbox
                key={item.id}
                label={`${item.title}（${item.relation}）`}
                checked={extra.includes(item.id)}
                onChange={(event) =>
                  setExtra((current) =>
                    event.currentTarget.checked
                      ? [...current, item.id]
                      : current.filter((id) => id !== item.id),
                  )
                }
              />
            ))}
          </Stack>
        )}
        {moved > 0 && (
          <Text size="sm">其中 {moved} 部已在别的合集，收入后会从原合集移出</Text>
        )}
        {save.isError && (
          <Alert color="red" title="没有收入">
            {save.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button loading={save.isPending} disabled={!canSave} onClick={() => save.mutate()}>
            {mode === "new" ? "建立并收入" : "收入"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
