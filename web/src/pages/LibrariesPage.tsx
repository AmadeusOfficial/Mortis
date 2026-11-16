import {
  Alert,
  Button,
  Group,
  Input,
  Loader,
  Modal,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type Library,
  createLibrary,
  deleteLibrary,
  fetchDataTypes,
  fetchLibraries,
  updateLibrary,
} from "../api";
import { LibraryCard } from "../components/libraries/LibraryCard";
import { PathPicker } from "../components/PathPicker";
import { PageHeader, Pill } from "../components/ui";
import { fileUnit, isPluginType, itemUnit, useDataTypes } from "../dataTypes";
import { IconPlus } from "../icons";
import classes from "./LibrariesPage.module.css";

export function LibrariesPage() {
  const libraries = useQuery({ queryKey: ["libraries"], queryFn: fetchLibraries });
  const dataTypes = useDataTypes();
  const [editing, setEditing] = useState<Library | "new" | null>(null);
  const [deleting, setDeleting] = useState<Library | null>(null);

  const list = libraries.data ?? [];
  const totalItems = list.reduce((sum, library) => sum + library.item_count, 0);

  return (
    <Stack gap={0}>
      <PageHeader
        title="媒体库"
        pills={
          libraries.isSuccess ? (
            <>
              <Pill>{list.length} 个库</Pill>
              <Pill tone="ok">{totalItems} 个条目</Pill>
            </>
          ) : undefined
        }
        description="按数据类型分库管理，独立扫描与刮削策略"
        actions={
          <Button
            variant="filled"
            leftSection={<IconPlus size={15} />}
            onClick={() => setEditing("new")}
          >
            新建媒体库
          </Button>
        }
      />

      {libraries.isPending && <Loader size="sm" />}

      {libraries.isError && (
        <Alert color="red" title="读不到媒体库">
          {libraries.error.message}
        </Alert>
      )}

      {libraries.isSuccess && list.length === 0 && (
        <div className={classes.emptyNotice}>
          还没有媒体库。新建一个电影库，选好视频所在的目录。
        </div>
      )}

      {libraries.isSuccess && (
        <div className={classes.grid}>
          {list.map((library) => (
            <LibraryCard
              key={library.id}
              library={library}
              itemUnit={itemUnit(dataTypes.data, library.data_type_id)}
              fileUnit={fileUnit(dataTypes.data, library.data_type_id)}
              canArrange={!isPluginType(dataTypes.data, library.data_type_id)}
              onEdit={() => setEditing(library)}
              onDelete={() => setDeleting(library)}
            />
          ))}

          <button
            type="button"
            className={classes.addCard}
            onClick={() => setEditing("new")}
          >
            <div className={classes.addInner}>
              <span className={classes.addIcon} aria-hidden="true">
                <IconPlus size={22} />
              </span>
              <span className={classes.addTitle}>新建媒体库</span>
              <span className={classes.addHint}>选择数据类型，点「浏览」指定目录</span>
            </div>
          </button>
        </div>
      )}

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
