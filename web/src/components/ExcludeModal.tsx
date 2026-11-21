import { Alert, Button, Group, Loader, Modal, Paper, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ExcludeTarget, excludeTarget, fetchExcludePreview } from "../api";

export function ExcludeModal({
  target,
  name,
  onClose,
  onDone,
}: {
  target: ExcludeTarget;
  name: string;
  onClose: () => void;
  onDone: (libraryId: number) => void;
}) {
  const queryClient = useQueryClient();
  const preview = useQuery({
    queryKey: ["exclude-preview", target.kind, target.id],
    queryFn: () => fetchExcludePreview(target),
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const run = useMutation({
    mutationFn: () => {
      if (preview.data === undefined) {
        throw new Error("还没读到要排除的目录");
      }
      return excludeTarget(target, preview.data.entries);
    },
    onSuccess: ({ library_id }) => {
      notifications.show({ color: "green", message: `已排除「${name}」，以后扫描不再收进来` });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
      void queryClient.invalidateQueries({ queryKey: ["wall"] });
      void queryClient.invalidateQueries({ queryKey: ["library", library_id] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      onDone(library_id);
    },
  });
  const reload = () => {
    run.reset();
    void preview.refetch();
  };

  const data = preview.data;
  const conflict = run.error instanceof ApiError && run.error.status === 409;
  return (
    <Modal opened onClose={onClose} title="排除识别" size="lg">
      <Stack>
        {preview.isFetching && <Loader size="sm" />}
        {preview.isError && (
          <Alert color="red" title="不能排除">
            <Text size="sm" style={{ wordBreak: "break-all" }}>
              {preview.error.message}
            </Text>
          </Alert>
        )}
        {data !== undefined && !preview.isFetching && (
          <>
            <Text size="sm">
              下面的路径会加进「{data.type_name} → 跳过的目录」，以后扫描时不再收进来，也不再刮削。
            </Text>
            <Paper withBorder p="xs">
              <Stack gap={2} aria-label="要排除的路径">
                {data.entries.map((entry) => (
                  <Text key={entry} size="xs" ff="monospace" style={{ wordBreak: "break-all" }}>
                    {entry}
                  </Text>
                ))}
              </Stack>
            </Paper>
            <Text size="sm">
              {data.title !== null
                ? `《${data.title}》和它的 ${data.file_count} 条文件记录会从库里去掉。`
                : `${data.file_count} 条未识别的文件记录会从库里去掉。`}
              磁盘上的文件不动。
            </Text>
            {data.reasons.length > 0 && (
              <Stack gap={2}>
                {data.reasons.map((reason) => (
                  <Text key={reason} size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
                    · {reason}
                  </Text>
                ))}
              </Stack>
            )}
          </>
        )}
        {run.isError && (
          <Alert color="red" title={conflict ? "没有排除" : "排除失败"}>
            <Stack gap="xs" align="flex-start">
              <Text size="sm" style={{ wordBreak: "break-all" }}>
                {run.error.message}
              </Text>
              {conflict && (
                <Button size="xs" variant="default" onClick={reload}>
                  重新读取
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
            loading={run.isPending}
            disabled={data === undefined || preview.isFetching || conflict}
            onClick={() => run.mutate()}
          >
            排除
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
