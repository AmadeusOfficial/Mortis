import { Alert, Button, FileButton, Group, Modal, Radio, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { type ItemDetail, setCoverMode, uploadImage } from "../../api";
import { CoverCollage } from "../CoverCollage";

type CoverMode = "auto" | "single" | "grid";

export function CoverModeModal({
  detail,
  onClose,
  onSaved,
}: {
  detail: ItemDetail;
  onClose: () => void;
  onSaved: (detail: ItemDetail) => void;
}) {
  const [mode, setMode] = useState<CoverMode>(detail.cover_mode ?? "auto");
  const [poster, setPoster] = useState<File | null>(null);
  const [cells, setCells] = useState<(File | null)[]>([null, null, null, null]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (mode === "single" && poster !== null) {
        await uploadImage(detail.id, "poster", poster);
      }
      if (mode === "grid") {
        for (const [index, file] of cells.entries()) {
          if (file !== null) {
            await uploadImage(detail.id, `cover_${index + 1}`, file);
          }
        }
      }
      onSaved(await setCoverMode(detail.id, mode));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal opened onClose={onClose} title="封面" size="md">
      <Stack gap="sm">
        <Radio.Group value={mode} onChange={(value) => setMode(value as CoverMode)}>
          <Stack gap={6}>
            <Radio value="auto" label="自动四宫格" />
            <Radio value="single" label="上传一张" />
            <Radio value="grid" label="上传四张" />
          </Stack>
        </Radio.Group>
        {mode === "auto" && <CoverCollage urls={detail.cover_urls ?? []} />}
        {mode === "single" && (
          <Group>
            <FileButton accept="image/jpeg,image/png,image/webp" onChange={setPoster}>
              {(props) => (
                <Button variant="default" {...props}>
                  选择图片
                </Button>
              )}
            </FileButton>
            {poster === null && detail.poster_url !== null && (
              <img
                src={detail.poster_url}
                alt="当前封面"
                style={{ width: 72, aspectRatio: "2 / 3", objectFit: "cover" }}
              />
            )}
            {poster !== null && <Text size="sm">{poster.name}</Text>}
          </Group>
        )}
        {mode === "grid" && (
          <Stack gap={6}>
            {cells.map((file, index) => (
              <Group key={index}>
                <Text size="sm" w={72}>
                  第 {index + 1} 格
                </Text>
                <FileButton
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(next) =>
                    setCells((current) => current.map((item, itemIndex) => (itemIndex === index ? next : item)))
                  }
                >
                  {(props) => (
                    <Button variant="default" size="xs" {...props}>
                      {file === null ? "选择图片" : file.name}
                    </Button>
                  )}
                </FileButton>
              </Group>
            ))}
            <Text size="sm" c="dimmed">
              空着的格子用成员海报补上。
            </Text>
          </Stack>
        )}
        {error !== null && (
          <Alert color="red" title="没有保存">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button loading={saving} onClick={() => void save()}>
            保存
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
