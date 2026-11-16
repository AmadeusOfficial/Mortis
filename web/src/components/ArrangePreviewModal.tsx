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
import type { ArrangePreview } from "../api";

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

function PreviewBlock({ preview }: { preview: ArrangePreview }) {
  const hasProblems = preview.problems.length > 0;
  const renamed = preview.moves.filter((move) => fileName(move.source) !== fileName(move.target));
  return (
    <Paper withBorder p="sm">
      <Stack gap="xs">
        <Text fw={500}>{preview.title}</Text>
        {hasProblems && (
          <Alert color="red" title="不会重命名">
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

export interface ArrangePlan {
  rename: boolean;
  write: boolean;
  scope: string;
  empty: string | null;
  previews: ArrangePreview[];
  canConfirm: boolean;
}

function Steps({ plan }: { plan: ArrangePlan }) {
  if (plan.empty !== null) {
    return <Text c="dimmed">{plan.empty}</Text>;
  }
  if (!plan.rename && !plan.write) {
    return (
      <Text c="dimmed">
        设置里「整理时重命名」和「整理时写 NFO 和图片」都关着，整理不会做任何事。
      </Text>
    );
  }
  const renamed = plan.previews.some((preview) => preview.changes);
  return (
    <Stack gap={4}>
      <Text size="sm">{plan.scope}会：</Text>
      {plan.rename && (
        <Text size="sm">
          · 按模板重命名目录和文件{renamed ? "，改动列在下面" : "（已经是目标名字，不用改）"}
        </Text>
      )}
      {plan.write && <Text size="sm">· 把 NFO、海报、背景图和演员头像写到视频旁边</Text>}
    </Stack>
  );
}

export function ArrangePreviewModal({
  opened,
  onClose,
  plan,
  loading,
  error,
  onConfirm,
  confirming,
}: {
  opened: boolean;
  onClose: () => void;
  plan: ArrangePlan | undefined;
  loading: boolean;
  error: string | null;
  onConfirm: () => void;
  confirming: boolean;
}) {
  const previews = (plan?.previews ?? []).filter(
    (preview) => preview.changes || preview.problems.length > 0,
  );
  return (
    <Modal opened={opened} onClose={onClose} title="整理预览" size="xl">
      <Stack>
        {loading && <Loader size="sm" />}
        {error !== null && (
          <Alert color="red" title="没法整理">
            {error}
          </Alert>
        )}
        {plan !== undefined && <Steps plan={plan} />}
        {previews.length > 0 && (
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
            disabled={loading || plan === undefined || !plan.canConfirm}
            onClick={onConfirm}
          >
            确认整理
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
