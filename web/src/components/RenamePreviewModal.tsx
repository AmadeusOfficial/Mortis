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
} from "@mantine/core";
import type { RenamePreview } from "../api";

function fileName(path: string): string {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return index === -1 ? path : path.slice(index + 1);
}

function PathText({ path }: { path: string }) {
  return (
    <Text size="xs" ff="monospace" title={path} style={{ wordBreak: "break-all" }}>
      {fileName(path)}
    </Text>
  );
}

function PreviewBlock({ preview }: { preview: RenamePreview }) {
  const hasProblems = preview.problems.length > 0;
  const renamed = preview.moves.filter((move) => fileName(move.source) !== fileName(move.target));
  return (
    <Paper withBorder p="sm">
      <Stack gap="xs">
        <Text fw={500}>{preview.title}</Text>
        {hasProblems && (
          <Alert color="red" title="会跳过">
            <Stack gap={2}>
              {preview.problems.map((problem) => (
                <Text key={problem} size="sm">
                  {problem}
                </Text>
              ))}
            </Stack>
          </Alert>
        )}
        {preview.make_folder && preview.folder_to !== null && (
          <Group gap={4} wrap="nowrap" align="baseline">
            <Text size="xs" c="dimmed">
              新建目录
            </Text>
            <PathText path={preview.folder_to} />
          </Group>
        )}
        {!preview.make_folder &&
          preview.folder_from !== null &&
          preview.folder_to !== null && (
            <Group gap={4} wrap="nowrap" align="baseline">
              <Text size="xs" c="dimmed">
                目录
              </Text>
              <PathText path={preview.folder_from} />
              <Text size="xs" c="dimmed">
                →
              </Text>
              <PathText path={preview.folder_to} />
            </Group>
          )}
        {renamed.length > 0 && (
          <Stack gap={2}>
            {renamed.map((move) => (
              <Group key={move.source} gap={4} wrap="nowrap" align="baseline">
                <PathText path={move.source} />
                <Text size="xs" c="dimmed">
                  →
                </Text>
                <PathText path={move.target} />
              </Group>
            ))}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}

export function RenamePreviewModal({
  opened,
  onClose,
  previews,
  loading,
  error,
  onConfirm,
  confirming,
}: {
  opened: boolean;
  onClose: () => void;
  previews: RenamePreview[] | undefined;
  loading: boolean;
  error: string | null;
  onConfirm: () => void;
  confirming: boolean;
}) {
  const pending = (previews ?? []).filter(
    (preview) => preview.problems.length === 0 && preview.changes,
  );
  const nothingToDo =
    previews !== undefined &&
    previews.every((preview) => preview.problems.length === 0 && !preview.changes);
  return (
    <Modal opened={opened} onClose={onClose} title="重命名预览" size="xl">
      <Stack>
        {loading && <Loader size="sm" />}
        {error !== null && (
          <Alert color="red" title="没法重命名">
            {error}
          </Alert>
        )}
        {nothingToDo && <Text c="dimmed">都已经是目标名字，不用改。</Text>}
        {previews !== undefined && previews.length > 0 && !nothingToDo && (
          <ScrollArea.Autosize mah={480}>
            <Stack gap="sm">
              {previews.map((preview) => (
                <PreviewBlock key={preview.item_id} preview={preview} />
              ))}
            </Stack>
          </ScrollArea.Autosize>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            loading={confirming}
            disabled={loading || pending.length === 0}
            onClick={onConfirm}
          >
            确认重命名
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
