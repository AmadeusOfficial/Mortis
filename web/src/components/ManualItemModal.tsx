import {
  Alert,
  Button,
  FileButton,
  Group,
  Modal,
  Stack,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
  type ManualItemCreate,
  type UnmatchedFile,
  createManualItem,
  fetchSourceOrders,
  uploadImage,
} from "../api";
import { IconClose, IconImage, IconPlus } from "../icons";
import classes from "./ManualItemModal.module.css";

const MAX_POSTER_BYTES = 20 * 1024 * 1024;

export function ManualItemModal({
  file,
  name,
  note,
  onClose,
}: {
  file: UnmatchedFile;
  name: string;
  // 这一行会归进哪些文件，按结构说明。
  note: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [title, setTitle] = useState(file.guess_title);
  const [originalTitle, setOriginalTitle] = useState("");
  const [releaseDate, setReleaseDate] = useState("");
  const [overview, setOverview] = useState("");
  const [poster, setPoster] = useState<File | null>(null);
  const [posterUrl, setPosterUrl] = useState<string | null>(null);
  // 来源顺序里带着这个类型作品层的字段目录；读不到时照常给选海报，传不上会提示。
  const orders = useQuery({
    queryKey: ["source-orders", file.data_type_id],
    queryFn: () => fetchSourceOrders(file.data_type_id),
  });
  const hasPoster =
    orders.isError || (orders.data?.fields.some((field) => field.key === "poster") ?? false);

  useEffect(() => {
    if (poster === null) {
      setPosterUrl(null);
      return;
    }
    const url = URL.createObjectURL(poster);
    setPosterUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [poster]);

  const create = useMutation({
    mutationFn: async () => {
      const body: ManualItemCreate = { title: title.trim() };
      if (originalTitle.trim() !== "") body.original_title = originalTitle.trim();
      if (releaseDate !== "") body.release_date = releaseDate;
      if (overview.trim() !== "") body.overview = overview.trim();
      const { item_id } = await createManualItem(file.id, body);
      let posterError: string | null = null;
      if (poster !== null && hasPoster) {
        try {
          await uploadImage(item_id, "poster", poster);
        } catch (error) {
          posterError = error instanceof Error ? error.message : String(error);
        }
      }
      return { itemId: item_id, posterError };
    },
    onSuccess: ({ itemId, posterError }) => {
      void queryClient.invalidateQueries({ queryKey: ["unmatched"] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      void queryClient.invalidateQueries({ queryKey: ["library", file.library_id] });
      void queryClient.invalidateQueries({ queryKey: ["wall", file.library_id] });
      if (posterError === null) {
        notifications.show({ color: "green", message: `已建手动条目《${title.trim()}》` });
      } else {
        notifications.show({
          color: "yellow",
          title: `已建手动条目《${title.trim()}》，海报没传上`,
          message: `${posterError}。可以在详情页再换海报。`,
        });
      }
      onClose();
      void navigate(`/items/${itemId}`);
    },
  });

  const posterTooBig = hasPoster && poster !== null && poster.size > MAX_POSTER_BYTES;
  const canSave = title.trim() !== "" && !posterTooBig;
  const guessYear =
    file.guess_year !== null ? `文件名里猜出的年份是 ${file.guess_year}` : undefined;

  return (
    <Modal
      opened
      onClose={() => {
        if (!create.isPending) onClose();
      }}
      centered
      size="lg"
      title={`手动建条目「${name}」`}
    >
      <Stack>
        <Group align="flex-start" wrap="nowrap" gap="md">
          {hasPoster && (
            <Stack gap={6} className={classes.posterCol}>
              <div className={classes.poster}>
                {posterUrl !== null ? (
                  <img src={posterUrl} alt="海报预览" />
                ) : (
                  <IconImage size={26} />
                )}
              </div>
              <FileButton
                accept="image/png,image/jpeg,image/webp"
                onChange={(picked) => picked !== null && setPoster(picked)}
              >
                {(props) => (
                  <Button
                    {...props}
                    size="xs"
                    variant="default"
                    leftSection={<IconPlus size={12} />}
                    disabled={create.isPending}
                  >
                    {poster === null ? "选海报" : "换一张"}
                  </Button>
                )}
              </FileButton>
              {poster !== null && (
                <Button
                  size="xs"
                  variant="subtle"
                  color="gray"
                  leftSection={<IconClose size={12} />}
                  disabled={create.isPending}
                  onClick={() => setPoster(null)}
                >
                  不要海报
                </Button>
              )}
            </Stack>
          )}
          <Stack gap="sm" style={{ flex: 1, minWidth: 0 }}>
            <TextInput
              label="标题"
              required
              data-autofocus
              value={title}
              onChange={(event) => setTitle(event.currentTarget.value)}
            />
            <TextInput
              label="原名"
              value={originalTitle}
              onChange={(event) => setOriginalTitle(event.currentTarget.value)}
            />
            <TextInput
              label="日期"
              type="date"
              description={guessYear}
              value={releaseDate}
              onChange={(event) => setReleaseDate(event.currentTarget.value)}
            />
          </Stack>
        </Group>
        <Textarea
          label="简介"
          autosize
          minRows={3}
          maxRows={8}
          value={overview}
          onChange={(event) => setOverview(event.currentTarget.value)}
        />
        <Text size="xs" c="dimmed">
          {note}
          填了的字段会锁定。手动条目没有来源编号，不能刮削；以后可以在详情页重新匹配。
        </Text>
        {posterTooBig && (
          <Alert color="red" title="海报太大">
            图片不能超过 20MB。
          </Alert>
        )}
        {create.isError && (
          <Alert color="red" title="没有建成">
            {create.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" disabled={create.isPending} onClick={onClose}>
            取消
          </Button>
          <Button loading={create.isPending} disabled={!canSave} onClick={() => create.mutate()}>
            建条目
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
