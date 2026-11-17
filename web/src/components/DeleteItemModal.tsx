import {
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { ApiError, deleteExpected, deleteItem, fetchDeletePreview } from "../api";
import { formatSize } from "../format";

const SHOWN = 8;

function PathList({ label, paths, total }: { label: string; paths: string[]; total?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? paths : paths.slice(0, SHOWN);
  const rest = (total ?? paths.length) - shown.length;
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        {label}
      </Text>
      <Paper withBorder p="xs">
        <ScrollArea.Autosize mah={220}>
          <Stack gap={2} aria-label={label}>
            {shown.map((path) => (
              <Text key={path} size="xs" ff="monospace" style={{ wordBreak: "break-all" }}>
                {path}
              </Text>
            ))}
          </Stack>
        </ScrollArea.Autosize>
        {rest > 0 &&
          (shown.length < paths.length ? (
            <Button variant="subtle" size="compact-xs" mt={4} onClick={() => setAll(true)}>
              还有 {rest} 个
            </Button>
          ) : (
            <Text size="xs" c="dimmed" mt={4}>
              还有 {rest} 个
            </Text>
          ))}
      </Paper>
    </Stack>
  );
}

export function DeleteItemModal({
  itemId,
  title,
  onClose,
}: {
  itemId: number;
  title: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [typed, setTyped] = useState("");
  const preview = useQuery({
    queryKey: ["item-delete-preview", itemId],
    queryFn: () => fetchDeletePreview(itemId),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const remove = useMutation({
    mutationFn: () => {
      if (preview.data === undefined) {
        throw new Error("还没读到要删的文件");
      }
      return deleteItem(itemId, typed.trim(), deleteExpected(preview.data));
    },
    onSuccess: ({ library_id }) => {
      notifications.show({ color: "green", message: `已删除《${title}》` });
      onClose();
      navigate(`/libraries/${library_id}`);
      queryClient.removeQueries({ queryKey: ["item", itemId] });
      void queryClient.invalidateQueries({ queryKey: ["wall"] });
      void queryClient.invalidateQueries({ queryKey: ["library", library_id] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ["item", itemId] });
      }
    },
  });
  const reload = () => {
    remove.reset();
    setTyped("");
    void preview.refetch();
  };

  const data = preview.data;
  const matches = data !== undefined && typed.trim() === data.title.trim();
  const conflict = remove.error instanceof ApiError && remove.error.status === 409;
  // 删一半失败时后端的说明以「有 N 个没删掉」开头。
  const partial = conflict && (remove.error?.message.includes("没删掉") ?? false);
  return (
    <Modal opened onClose={onClose} title="删除条目" size="lg">
      <Stack>
        {preview.isFetching && <Loader size="sm" />}
        {preview.isError && (
          <Alert color="red" title="不能删除">
            <Stack gap="xs" align="flex-start">
              <Text size="sm">{preview.error.message}</Text>
              <Button size="xs" variant="default" onClick={reload}>
                重新读取
              </Button>
            </Stack>
          </Alert>
        )}
        {data !== undefined && !preview.isFetching && (
          <>
            <Text>
              删除《{data.title}》？库里的记录和下面这些本地文件会一起删掉。
            </Text>
            <Group gap="lg">
              <Text size="sm">
                文件数 <b>{data.file_count}</b>
              </Text>
              <Text size="sm">
                总大小 <b>{formatSize(data.total_size)}</b>
              </Text>
            </Group>
            {data.permanent ? (
              <Alert color="red" title="这个盘没有回收站，会永久删除" />
            ) : (
              <Text size="sm" c="dimmed">
                文件会移到回收站，系统可能会弹出确认框
              </Text>
            )}
            {data.folders.length > 0 && <PathList label="整个删掉的文件夹" paths={data.folders} />}
            {data.files.length > 0 && <PathList label="删掉的文件" paths={data.files} />}
            {data.folders.length === 0 && data.files.length === 0 && (
              <Text size="sm" c="dimmed">
                这个条目没有本地文件，只删库里的记录。
              </Text>
            )}
            {data.unregistered.count > 0 && (
              <PathList
                label={`文件夹里没登记的文件也会一起删（${data.unregistered.count} 个，${formatSize(data.unregistered.total_size)}）`}
                paths={data.unregistered.sample}
                total={data.unregistered.count}
              />
            )}
            {data.reasons.length > 0 && (
              <Stack gap={2}>
                {data.reasons.map((reason) => (
                  <Text key={reason} size="xs" c="dimmed">
                    · {reason}
                  </Text>
                ))}
              </Stack>
            )}
            <TextInput
              label={`输入「${data.title}」确认`}
              placeholder={data.title}
              value={typed}
              onChange={(event) => setTyped(event.currentTarget.value)}
              data-autofocus
            />
          </>
        )}
        {remove.isError && (
          <Alert color="red" title={partial ? "没有全部删掉" : conflict ? "没有删除" : "删除失败"}>
            <Stack gap="xs" align="flex-start">
              <Text size="sm" style={{ wordBreak: "break-all" }}>
                {remove.error.message}
              </Text>
              {conflict && (
                <Button size="xs" variant="default" onClick={reload}>
                  重新读取要删的文件
                </Button>
              )}
            </Stack>
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            color="red"
            loading={remove.isPending}
            disabled={!matches || preview.isFetching || conflict}
            onClick={() => remove.mutate()}
          >
            删除
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
