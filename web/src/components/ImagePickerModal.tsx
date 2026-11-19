import {
  Alert,
  Button,
  FileButton,
  Group,
  Loader,
  Modal,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type FieldOut,
  type ImageChoice,
  type ItemDetail,
  chooseImage,
  fetchImageChoices,
  uploadImage,
} from "../api";
import { IconCheck, IconPlus } from "../icons";
import classes from "./ImagePickerModal.module.css";

export const TRANSPARENT_KEYS = new Set([
  "clearlogo",
  "clearart",
  "logo",
  "characterart",
]);
const WIDE_KEYS = new Set(["fanart", "thumb", "landscape", "banner"]);

const languageNames = (() => {
  try {
    return new Intl.DisplayNames(["zh-CN"], { type: "language" });
  } catch {
    return null;
  }
})();

// zh + TW 显示成「中文（台湾）」；没有语言的是无字图。
export function languageLabel(language: string | null, region: string | null): string {
  if (language === null) {
    return "无文字";
  }
  const code = region === null ? language : `${language}-${region}`;
  try {
    return languageNames?.of(code) ?? code;
  } catch {
    return code;
  }
}

function ChoiceGrid({
  itemId,
  field,
  manual,
  onChanged,
}: {
  itemId: number;
  field: FieldOut;
  manual: boolean;
  onChanged: (detail: ItemDetail, message: string) => void;
}) {
  const [url, setUrl] = useState("");
  const choices = useQuery({
    queryKey: ["image-choices", itemId, field.key],
    queryFn: () => fetchImageChoices(itemId, field.key),
  });
  const choose = useMutation({
    mutationFn: (target: string) => chooseImage(itemId, field.key, target),
    onSuccess: (detail) => {
      setUrl("");
      onChanged(detail, `已换上「${field.label}」并锁定`);
    },
  });
  const upload = useMutation({
    mutationFn: (file: File) => uploadImage(itemId, field.key, file),
    onSuccess: (detail) => onChanged(detail, `已上传「${field.label}」并锁定`),
  });
  const busy = choose.isPending || upload.isPending;
  const error = choose.error ?? upload.error;
  const picClass = `${classes.pic} ${TRANSPARENT_KEYS.has(field.key) ? classes.checker : ""} ${
    WIDE_KEYS.has(field.key) ? classes.wide : field.key === "clearlogo" ? classes.logo : ""
  }`;

  const card = (choice: ImageChoice) => (
    <button
      key={choice.url}
      type="button"
      className={`${classes.card} ${choice.selected ? classes.selected : ""}`}
      disabled={busy}
      aria-pressed={choice.selected}
      aria-label={`用这张${field.label}：${choice.source_name}，${languageLabel(choice.language, choice.region)}`}
      onClick={() => {
        if (!choice.selected) {
          choose.mutate(choice.url);
        }
      }}
    >
      <div className={picClass}>
        <img src={choice.preview_url} alt="" loading="lazy" />
        {choice.selected && (
          <span className={classes.mark}>
            <IconCheck size={14} /> 当前
          </span>
        )}
        {choose.isPending && choose.variables === choice.url && (
          <span className={classes.busy}>
            <Loader size="sm" />
          </span>
        )}
      </div>
      <div className={classes.cap}>
        <span className={classes.lang}>
          {languageLabel(choice.language, choice.region)}
        </span>
        <span>
          {choice.source_name}
          {choice.width !== null && choice.height !== null
            ? ` · ${choice.width}×${choice.height}`
            : ""}
        </span>
      </div>
    </button>
  );

  return (
    <>
      <Group gap="sm" align="flex-end" mb="md" wrap="nowrap">
        <FileButton
          accept="image/png,image/jpeg,image/webp"
          onChange={(file) => file !== null && upload.mutate(file)}
        >
          {(props) => (
            <Button
              {...props}
              variant="default"
              leftSection={<IconPlus size={14} />}
              loading={upload.isPending}
              disabled={busy}
            >
              上传图片
            </Button>
          )}
        </FileButton>
        <TextInput
          style={{ flex: 1 }}
          placeholder="或粘贴图片网址：https://…"
          aria-label="图片网址"
          value={url}
          onChange={(event) => setUrl(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && url.trim() !== "") {
              choose.mutate(url.trim());
            }
          }}
        />
        <Button
          variant="default"
          disabled={busy || url.trim() === ""}
          loading={choose.isPending && choose.variables === url.trim()}
          onClick={() => choose.mutate(url.trim())}
        >
          从网址添加
        </Button>
      </Group>
      {error !== null && (
        <Alert color="red" mb="md" title="没有换成">
          {error.message}
        </Alert>
      )}
      {choices.isPending ? (
        <Loader size="sm" />
      ) : choices.isError ? (
        <Alert color="red" title="读不到候选">
          {choices.error.message}
        </Alert>
      ) : choices.data.length === 0 ? (
        <Text size="sm" c="dimmed">
          {manual
            ? "手动条目没有来源，可以上传本地图片，或者粘贴网址。"
            : "来源快照里没有这种图。先刮削一次，或者上传、粘贴网址。"}
        </Text>
      ) : (
        <>
          <Text size="xs" c="dimmed" mb="xs">
            {choices.data.length} 张候选，按来源顺序和语言排列。点一张立刻换上，换过的字段会锁定，再次刮削不覆盖。
          </Text>
          <div className={classes.grid}>{choices.data.map(card)}</div>
        </>
      )}
    </>
  );
}

// 照 Plex：每种图一个页签，点缩略图立刻生效；另可上传或填网址。
export function ImagePickerModal({
  itemId,
  fields,
  initialKey,
  manual = false,
  onChanged,
  onClose,
}: {
  itemId: number;
  fields: FieldOut[];
  initialKey: string;
  manual?: boolean;
  onChanged: (detail: ItemDetail) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<string | null>(initialKey);
  const changed = (detail: ItemDetail, message: string) => {
    onChanged(detail);
    void queryClient.invalidateQueries({ queryKey: ["image-choices", itemId] });
    notifications.show({ color: "green", message });
  };
  return (
    <Modal opened onClose={onClose} title="编辑图片" size="80rem">
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List mb="md">
          {fields.map((field) => (
            <Tabs.Tab key={field.key} value={field.key}>
              {field.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {fields.map((field) => (
          <Tabs.Panel key={field.key} value={field.key}>
            <ChoiceGrid itemId={itemId} field={field} manual={manual} onChanged={changed} />
          </Tabs.Panel>
        ))}
      </Tabs>
    </Modal>
  );
}
