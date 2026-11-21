import { Alert, Button, Group, Loader, Modal, ScrollArea, Stack, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type EmptyFolder, deleteEmptyFolders, fetchEmptyFolders } from "../../api";

function leaf(path: string): string {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return index === -1 ? path : path.slice(index + 1);
}

// 删空文件夹：先列出整个子树没有文件的目录，确认后只删目录，不碰文件。
export function EmptyFolderModal({
  libraryId,
  opened,
  onClose,
}: {
  libraryId: number;
  opened: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const folders = useQuery({
    queryKey: ["empty-folders", libraryId],
    queryFn: () => fetchEmptyFolders(libraryId),
    enabled: opened,
  });
  const removing = useMutation({
    mutationFn: (paths: string[]) => deleteEmptyFolders(libraryId, paths),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["empty-folders", libraryId] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      onClose();
    },
  });

  const paths =
    folders.data === undefined
      ? []
      : folders.data.folders.filter((f: EmptyFolder) => f.nested > 0).map((f) => f.path);

  return (
    <Modal opened={opened} onClose={onClose} title="清理空文件夹" size="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          只删「整个文件夹里一个文件都没有」的目录，不删任何文件。以 . 开头的目录（如
          .deletedByTMM）和下载临时目录会跳过。
        </Text>
        {folders.isPending && <Loader size="sm" />}
        {folders.isError && (
          <Alert color="red" title="读不到目录">
            {folders.error.message}
          </Alert>
        )}
        {folders.data !== undefined && folders.data.total === 0 && (
          <Text size="sm">没有空文件夹，库目录是干净的。</Text>
        )}
        {folders.data !== undefined && folders.data.total > 0 && (
          <>
            <Text size="sm">
              找到 {folders.data.folders.length} 个空文件夹，共 {folders.data.total} 个目录（含里面的空子目录）。
            </Text>
            <ScrollArea.Autosize mah={360}>
              <Stack gap={2}>
                {folders.data.folders.map((folder) => (
                  <Group key={folder.path} gap={6} wrap="nowrap" align="baseline">
                    <Text size="xs" ff="monospace" title={folder.path} style={{ wordBreak: "break-all" }}>
                      {leaf(folder.path)}
                    </Text>
                    {folder.nested > 1 && (
                      <Text size="xs" c="dimmed">
                        含 {folder.nested - 1} 个空子目录
                      </Text>
                    )}
                  </Group>
                ))}
              </Stack>
            </ScrollArea.Autosize>
          </>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            loading={removing.isPending}
            disabled={folders.data === undefined || paths.length === 0}
            onClick={() => removing.mutate(paths)}
          >
            删除这些空文件夹
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
