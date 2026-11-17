import { Badge, Modal, Stack, Text, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { type PlayCandidate, type PlayRequest, playCandidates, playItem } from "../api";
import { formatSize } from "../format";
import { IconFolder, IconGame, IconVideo } from "../icons";

interface PlayOptions {
  // 游戏条目：按钮叫「启动」，候选是游戏文件夹里的程序。
  game?: boolean;
  // 游戏条目：不用记住的程序，重新选。
  choose?: boolean;
}

interface Variables {
  itemId: number;
  title: string;
  game: boolean;
  body: PlayRequest;
}

interface Choosing {
  itemId: number;
  title: string;
  game: boolean;
  choose: boolean;
  candidates: PlayCandidate[];
}

function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function hasTarget(candidate: PlayCandidate): candidate is PlayCandidate & { target: string } {
  return typeof candidate.target === "string" && candidate.target !== "";
}

// 播放或启动一个条目：只有一个候选时直接打开；多个时后端回候选，弹窗让人选，不替人猜。
export function usePlayItem(): {
  play: (itemId: number, title: string, options?: PlayOptions) => void;
  playingId: number | null;
  rechoosingId: number | null;
  chooser: ReactNode;
} {
  const [choosing, setChoosing] = useState<Choosing | null>(null);
  const mutation = useMutation({
    mutationFn: ({ itemId, body }: Variables) => playItem(itemId, body),
    onSuccess: (result, variables) => {
      setChoosing(null);
      const name = baseName(result.path);
      notifications.show({
        color: "green",
        message: variables.game ? `已启动 ${name}` : `已用 ${result.player_name} 打开 ${name}`,
      });
    },
    onError: (error, variables) => {
      const candidates = playCandidates(error);
      if (candidates !== null) {
        setChoosing({
          itemId: variables.itemId,
          title: variables.title,
          game: variables.game,
          choose: variables.body.choose === true,
          candidates,
        });
        return;
      }
      notifications.show({
        color: "red",
        title: variables.game ? "启动失败" : "播放失败",
        message: error.message,
      });
    },
  });

  const programs = choosing !== null && choosing.candidates.some(hasTarget);
  const verb = choosing?.game ? "启动" : "播放";
  const title =
    choosing === null
      ? ""
      : `${programs ? "选择启动程序" : choosing.game ? "选择要启动的游戏文件夹" : "选择要播放的版本"} · ${choosing.title}`;
  const hint =
    choosing === null
      ? ""
      : programs
        ? choosing.candidates.length === 1
          ? "只找到这一个可以启动的程序，确认后启动，下次直接用它。"
          : `有 ${choosing.candidates.length} 个可以启动的程序，选一个启动，下次直接用它。`
        : choosing.game
          ? `有 ${choosing.candidates.length} 个游戏文件夹，选一个启动。`
          : `有 ${choosing.candidates.length} 个视频文件，选一个打开。`;

  const pick = (current: Choosing, candidate: PlayCandidate) =>
    mutation.mutate({
      itemId: current.itemId,
      title: current.title,
      game: current.game,
      body: hasTarget(candidate)
        ? { file_id: candidate.file_id, target: candidate.target }
        : current.choose
          ? { file_id: candidate.file_id, choose: true }
          : { file_id: candidate.file_id },
    });

  const chooser = (
    <Modal
      opened={choosing !== null}
      onClose={() => setChoosing(null)}
      title={title}
      size="lg"
    >
      {choosing !== null && (
        <Stack gap="xs">
          <Text size="sm" c="dimmed">
            {hint}
          </Text>
          {choosing.candidates.map((candidate) => {
            const label = hasTarget(candidate) ? candidate.target : candidate.name;
            return (
              <UnstyledButton
                key={`${candidate.file_id}:${candidate.target ?? ""}`}
                aria-label={`${verb} ${label}`}
                disabled={!candidate.exists || mutation.isPending}
                onClick={() => pick(choosing, candidate)}
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
                {hasTarget(candidate) ? (
                  <IconGame size={18} />
                ) : choosing.game ? (
                  <IconFolder size={18} />
                ) : (
                  <IconVideo size={18} />
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Text size="sm" fw={600} truncate className={hasTarget(candidate) ? "mo-mono" : undefined}>
                    {label}
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
                    {choosing.game ? "文件夹不在了" : "文件不在了"}
                  </Badge>
                )}
              </UnstyledButton>
            );
          })}
        </Stack>
      )}
    </Modal>
  );

  const pending = mutation.isPending ? mutation.variables : undefined;
  return {
    play: (itemId, title, options = {}) =>
      mutation.mutate({
        itemId,
        title,
        game: options.game === true,
        body: options.choose === true ? { choose: true } : {},
      }),
    playingId: pending !== undefined && pending.body.choose !== true ? pending.itemId : null,
    rechoosingId: pending !== undefined && pending.body.choose === true ? pending.itemId : null,
    chooser,
  };
}
