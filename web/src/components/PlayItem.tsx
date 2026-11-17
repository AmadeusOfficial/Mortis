import { Badge, Modal, Stack, Text, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { type PlayCandidate, playCandidates, playItem } from "../api";
import { formatSize } from "../format";
import { IconVideo } from "../icons";

interface Choosing {
  itemId: number;
  title: string;
  candidates: PlayCandidate[];
}

// 播放一个条目：只有一个视频时直接打开；多个时后端回候选，弹窗让人选，不替人猜。
export function usePlayItem(): {
  play: (itemId: number, title: string) => void;
  playingId: number | null;
  chooser: ReactNode;
} {
  const [choosing, setChoosing] = useState<Choosing | null>(null);
  const mutation = useMutation({
    mutationFn: ({ itemId, fileId }: { itemId: number; title: string; fileId: number | null }) =>
      playItem(itemId, fileId),
    onSuccess: (result) => {
      setChoosing(null);
      const name = result.path.split(/[\\/]/).pop() ?? result.path;
      notifications.show({ color: "green", message: `已用 ${result.player_name} 打开 ${name}` });
    },
    onError: (error, variables) => {
      const candidates = playCandidates(error);
      if (candidates !== null) {
        setChoosing({ itemId: variables.itemId, title: variables.title, candidates });
        return;
      }
      notifications.show({ color: "red", title: "播放失败", message: error.message });
    },
  });

  const chooser = (
    <Modal
      opened={choosing !== null}
      onClose={() => setChoosing(null)}
      title={choosing !== null ? `选择要播放的版本 · ${choosing.title}` : "选择要播放的版本"}
      size="lg"
    >
      {choosing !== null && (
        <Stack gap="xs">
          <Text size="sm" c="dimmed">
            有 {choosing.candidates.length} 个视频文件，选一个打开。
          </Text>
          {choosing.candidates.map((candidate) => (
            <UnstyledButton
              key={candidate.file_id}
              aria-label={`播放 ${candidate.name}`}
              disabled={!candidate.exists || mutation.isPending}
              onClick={() =>
                mutation.mutate({
                  itemId: choosing.itemId,
                  title: choosing.title,
                  fileId: candidate.file_id,
                })
              }
              style={{
                display: "flex",
                gap: 12,
                alignItems: "center",
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid var(--mo-line)",
                opacity: candidate.exists ? 1 : 0.55,
                cursor: candidate.exists ? "pointer" : "not-allowed",
              }}
            >
              <IconVideo size={18} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <Text size="sm" fw={600} truncate>
                  {candidate.name}
                </Text>
                <Text size="xs" c="dimmed" truncate title={candidate.path}>
                  {candidate.path}
                </Text>
              </div>
              {candidate.exists ? (
                <Text size="xs" c="dimmed" className="mo-mono">
                  {formatSize(candidate.size)}
                </Text>
              ) : (
                <Badge size="sm" variant="light" color="red">
                  文件不在了
                </Badge>
              )}
            </UnstyledButton>
          ))}
        </Stack>
      )}
    </Modal>
  );

  return {
    play: (itemId, title) => mutation.mutate({ itemId, title, fileId: null }),
    playingId: mutation.isPending ? (mutation.variables?.itemId ?? null) : null,
    chooser,
  };
}
