import {
  ActionIcon,
  Alert,
  Button,
  Combobox,
  Group,
  Modal,
  Stack,
  Text,
  TextInput,
  useCombobox,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type KeyboardEvent, useState } from "react";
import { fetchUserTags, saveItemUserTags } from "../../api";
import { IconPlus, IconTrash } from "../../icons";

const NAME_LIMIT = 40;

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

// 照 Infuse 的编辑列表：「+」出输入框，回车或选中提示就加进列表，保存才整组提交。
export function UserTagsModal({
  itemId,
  dataType,
  initial,
  onClose,
}: {
  itemId: number;
  dataType: string;
  initial: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [names, setNames] = useState<string[]>(initial);
  const [adding, setAdding] = useState(false);
  const [input, setInput] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const known = useQuery({
    queryKey: ["user-tags", dataType],
    queryFn: () => fetchUserTags(dataType),
  });
  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
  });
  const save = useMutation({
    mutationFn: () => saveItemUserTags(itemId, names),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["item", itemId] });
      void queryClient.invalidateQueries({ queryKey: ["user-tags", dataType] });
      onClose();
    },
  });

  const typed = input.trim();
  const options = (known.data ?? [])
    .map((tag) => tag.name)
    .filter(
      (name) =>
        !names.some((picked) => same(picked, name)) &&
        name.toLowerCase().includes(typed.toLowerCase()),
    );

  const add = (raw: string) => {
    const name = raw.trim();
    if (name === "") {
      return;
    }
    if (name.length > NAME_LIMIT) {
      setProblem(`标签名最多 ${NAME_LIMIT} 个字`);
      return;
    }
    setProblem(null);
    const existing = known.data?.find((tag) => same(tag.name, name))?.name ?? name;
    if (!names.some((picked) => same(picked, existing))) {
      setNames([...names, existing]);
    }
    setInput("");
    combobox.closeDropdown();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229) {
      return;
    }
    // 用方向键选中了提示时交给下拉框，由 onOptionSubmit 加入。
    if (combobox.dropdownOpened && combobox.getSelectedOptionIndex() !== -1) {
      return;
    }
    event.preventDefault();
    add(input);
  };

  return (
    <Modal
      opened
      onClose={onClose}
      title={
        <Group gap={6}>
          <span>我的标签</span>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label="添加标签"
            onClick={() => setAdding(true)}
          >
            <IconPlus size={14} />
          </ActionIcon>
        </Group>
      }
    >
      <Stack gap="sm">
        {adding && (
          <Combobox
            store={combobox}
            onOptionSubmit={(value) => add(value)}
          >
            <Combobox.Target>
              <TextInput
                aria-label="新标签"
                placeholder="输入标签名，回车加入"
                value={input}
                autoFocus
                onChange={(event) => {
                  setInput(event.currentTarget.value);
                  combobox.openDropdown();
                  combobox.resetSelectedOption();
                }}
                onFocus={() => combobox.openDropdown()}
                onBlur={() => combobox.closeDropdown()}
                onKeyDown={onKeyDown}
              />
            </Combobox.Target>
            <Combobox.Dropdown hidden={options.length === 0}>
              <Combobox.Options mah={220} style={{ overflowY: "auto" }}>
                {options.map((name) => (
                  <Combobox.Option value={name} key={name}>
                    {name}
                  </Combobox.Option>
                ))}
              </Combobox.Options>
            </Combobox.Dropdown>
          </Combobox>
        )}
        {problem !== null && (
          <Text size="xs" c="red">
            {problem}
          </Text>
        )}
        {names.length === 0 ? (
          <Text size="sm" c="dimmed">
            还没有标签，点「+」添加。
          </Text>
        ) : (
          <Stack gap={2} role="list" aria-label="标签列表">
            {names.map((name) => (
              <Group key={name} justify="space-between" wrap="nowrap" role="listitem">
                <Text size="sm">{name}</Text>
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="gray"
                  aria-label={`删除标签${name}`}
                  onClick={() => setNames(names.filter((picked) => picked !== name))}
                >
                  <IconTrash size={14} />
                </ActionIcon>
              </Group>
            ))}
          </Stack>
        )}
        {save.isError && (
          <Alert color="red" title="保存失败">
            {save.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            保存
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
