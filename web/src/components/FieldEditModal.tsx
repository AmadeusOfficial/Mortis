import {
  Alert,
  Button,
  Group,
  Modal,
  NumberInput,
  Stack,
  TagsInput,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import { useState } from "react";
import type { FieldOut } from "../api";

export function FieldEditModal({
  field,
  saving,
  error,
  onSave,
  onClose,
}: {
  field: FieldOut;
  saving: boolean;
  error: string | null;
  onSave: (value: unknown) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(
    field.value === null || Array.isArray(field.value) ? "" : String(field.value),
  );
  const [tags, setTags] = useState<string[]>(
    Array.isArray(field.value) ? (field.value as string[]) : [],
  );
  const [number, setNumber] = useState<number | "">(
    typeof field.value === "number" ? field.value : "",
  );
  const save = () => {
    if (field.value_type === "tags") {
      onSave(tags);
    } else if (field.value_type === "number") {
      onSave(number === "" ? null : number);
    } else {
      onSave(text.trim() === "" ? null : text);
    }
  };
  return (
    <Modal opened onClose={onClose} title={`修改「${field.label}」`}>
      <Stack>
        {field.value_type === "tags" && (
          <TagsInput
            label={field.label}
            value={tags}
            onChange={setTags}
            description="回车添加一项"
            data-autofocus
          />
        )}
        {field.value_type === "number" && (
          <NumberInput
            label={field.label}
            value={number}
            data-autofocus
            onChange={(value) => setNumber(typeof value === "number" ? value : "")}
          />
        )}
        {field.value_type === "longtext" && (
          <Textarea
            label={field.label}
            value={text}
            autosize
            minRows={4}
            data-autofocus
            onChange={(event) => setText(event.currentTarget.value)}
          />
        )}
        {["text", "url", "date"].includes(field.value_type) && (
          <TextInput
            label={field.label}
            value={text}
            data-autofocus
            placeholder={field.value_type === "date" ? "1999-03-30" : undefined}
            onChange={(event) => setText(event.currentTarget.value)}
          />
        )}
        <Text size="xs" c="dimmed">
          手动修改的字段会被锁定，重新刮削时不会覆盖。
        </Text>
        {error !== null && (
          <Alert color="red" title="保存失败">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button loading={saving} onClick={save}>
            保存
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
