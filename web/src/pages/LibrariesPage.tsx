import {
  Alert,
  Badge,
  Button,
  Group,
  Input,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import {
  type Library,
  createLibrary,
  deleteLibrary,
  fetchDataTypes,
  fetchLibraries,
  updateLibrary,
} from "../api";
import { PathPicker } from "../components/PathPicker";
import { fileUnit, itemUnit, useDataTypes } from "../dataTypes";
import { LibraryActions } from "../libraryActions";

const AUTOMATION_LABELS: Record<string, string> = {
  manual: "只手动扫描",
  watch: "发现新文件就整理",
  schedule: "定时扫描",
};

export function LibrariesPage() {
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const dataTypes = useDataTypes();
  const [editing, setEditing] = useState<Library | "new" | null>(null);
  const [deleting, setDeleting] = useState<Library | null>(null);

  return (
    <Stack maw={900}>
      <Group justify="space-between">
        <Title order={2}>媒体库</Title>
        <Button onClick={() => setEditing("new")}>新建媒体库</Button>
      </Group>
      {libraries.isPending && <Loader size="sm" />}
      {libraries.isError && (
        <Alert color="red" title="读不到媒体库">
          {libraries.error.message}
        </Alert>
      )}
      {libraries.isSuccess && libraries.data.length === 0 && (
        <Text c="dimmed">还没有媒体库。新建一个电影库，选好视频所在的目录。</Text>
      )}
      {libraries.isSuccess &&
        libraries.data.map((library) => (
          <LibraryCard
            key={library.id}
            library={library}
            itemUnit={itemUnit(dataTypes.data, library.data_type_id)}
            fileUnit={fileUnit(dataTypes.data, library.data_type_id)}
            onEdit={() => setEditing(library)}
            onDelete={() => setDeleting(library)}
          />
        ))}
      {editing !== null && (
        <LibraryFormModal
          library={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      {deleting !== null && (
        <DeleteLibraryModal
          library={deleting}
          itemUnit={itemUnit(dataTypes.data, deleting.data_type_id)}
          fileUnit={fileUnit(dataTypes.data, deleting.data_type_id)}
          onClose={() => setDeleting(null)}
        />
      )}
    </Stack>
  );
}

function LibraryCard({
  library,
  itemUnit,
  fileUnit,
  onEdit,
  onDelete,
}: {
  library: Library;
  itemUnit: string;
  fileUnit: string;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: (isAutoScrape: boolean) =>
      updateLibrary(library.id, { is_auto_scrape: isAutoScrape }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      notifications.show({
        color: "green",
        message: saved.is_auto_scrape ? "已打开扫描后自动刮削" : "已关闭扫描后自动刮削",
      });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={4}>
          <Group gap="xs">
            <Title order={4}>{library.name}</Title>
            <Badge variant="light">{library.data_type_name}</Badge>
          </Group>
          <Text size="sm" c="dimmed">
            {library.item_count} {itemUnit} · {library.file_count} {fileUnit}
          </Text>
          {library.paths.map((path) => (
            <Text key={path} size="sm" ff="monospace">
              {path}
            </Text>
          ))}
          <Text size="xs" c="dimmed">
            {AUTOMATION_LABELS[library.automation] ?? library.automation}
          </Text>
          <Switch
            label="扫描后自动刮削"
            checked={library.is_auto_scrape}
            disabled={toggle.isPending}
            onChange={(event) => toggle.mutate(event.currentTarget.checked)}
          />
        </Stack>
        <Stack gap="xs" align="flex-end">
          <Group gap="xs" wrap="nowrap">
            <Button component={Link} to={`/libraries/${library.id}`} size="xs" variant="default">
              打开
            </Button>
            <LibraryActions library={library} />
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Button variant="default" size="xs" onClick={onEdit}>
              编辑
            </Button>
            <Button variant="subtle" color="red" size="xs" onClick={onDelete}>
              删除
            </Button>
          </Group>
        </Stack>
      </Group>
    </Paper>
  );
}

function LibraryFormModal({ library, onClose }: { library: Library | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const dataTypes = useQuery({ queryKey: ["data-types"], queryFn: fetchDataTypes });
  const [name, setName] = useState(library?.name ?? "电影");
  const [dataTypeId, setDataTypeId] = useState<string | null>(library?.data_type_id ?? "movie");
  // 用户在新建时手动改过库名后，就不再跟随数据类型的名字。
  const [nameEdited, setNameEdited] = useState(false);
  const [paths, setPaths] = useState<string[]>(
    library !== null && library.paths.length > 0 ? library.paths : [""],
  );
  const setPathAt = (index: number, value: string) =>
    setPaths((current) => current.map((path, i) => (i === index ? value : path)));
  const dataTypeName = dataTypes.data?.find((type) => type.id === dataTypeId)?.name ?? "";
  const save = useMutation({
    mutationFn: () => {
      const filled = paths.map((path) => path.trim()).filter((path) => path !== "");
      return library === null
        ? createLibrary({ name, data_type_id: dataTypeId ?? "", paths: filled })
        : updateLibrary(library.id, { name, paths: filled });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      notifications.show({
        color: "green",
        message: library === null ? "媒体库已新建" : "媒体库已保存",
      });
      onClose();
    },
  });

  return (
    <Modal
      opened
      onClose={onClose}
      title={library === null ? "新建媒体库" : `编辑「${library.name}」`}
    >
      <Stack>
        <TextInput
          label="库名"
          placeholder={dataTypeName}
          value={name}
          onChange={(event) => {
            setNameEdited(true);
            setName(event.currentTarget.value);
          }}
        />
        <Select
          label="数据类型"
          data={(dataTypes.data ?? []).map((type) => ({ value: type.id, label: type.name }))}
          value={dataTypeId}
          onChange={(value) => {
            setDataTypeId(value);
            // 新建且没手动改过库名时，库名跟着所选类型的名字走。
            if (library === null && !nameEdited && value !== null) {
              setName(dataTypes.data?.find((type) => type.id === value)?.name ?? "");
            }
          }}
          allowDeselect={false}
          disabled={library !== null}
          description={library !== null ? "建好后不能改类型" : undefined}
        />
        <Input.Wrapper label="目录" description="视频所在目录的完整路径，可以点「浏览」选">
          <Stack gap="xs" mt={4}>
            {paths.map((path, index) => (
              <Group key={index} gap="xs" wrap="nowrap">
                <PathPicker
                  label={`目录 ${index + 1}`}
                  value={path}
                  placeholder="例如 M:\plex\movie"
                  onChange={(value) => setPathAt(index, value)}
                />
                <Button
                  variant="subtle"
                  color="red"
                  disabled={paths.length === 1}
                  onClick={() => setPaths((current) => current.filter((_, i) => i !== index))}
                >
                  删除
                </Button>
              </Group>
            ))}
            <Group>
              <Button
                variant="light"
                size="xs"
                onClick={() => setPaths((current) => [...current, ""])}
              >
                添加目录
              </Button>
            </Group>
          </Stack>
        </Input.Wrapper>
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

function DeleteLibraryModal({
  library,
  itemUnit,
  fileUnit,
  onClose,
}: {
  library: Library;
  itemUnit: string;
  fileUnit: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => deleteLibrary(library.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      notifications.show({ color: "green", message: `已删除媒体库「${library.name}」` });
      onClose();
    },
  });

  return (
    <Modal opened onClose={onClose} title="删除媒体库">
      <Stack>
        <Text>
          删除媒体库「{library.name}」？库里的 {library.item_count} {itemUnit}和{" "}
          {library.file_count} {fileUnit}的记录会一起删除，硬盘上的文件不动。
        </Text>
        {remove.isError && (
          <Alert color="red" title="删除失败">
            {remove.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button color="red" loading={remove.isPending} onClick={() => remove.mutate()}>
            确认删除
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
