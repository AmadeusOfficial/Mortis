import {
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  NavLink,
  ScrollArea,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchDirs } from "../api";

type PickMode = "dir" | "file";

// 选文件时从文件所在的目录开始浏览。
function startFolder(value: string, mode: PickMode): string {
  if (mode === "dir") return value;
  const cut = value.replace(/[\\/][^\\/]*$/, "");
  if (cut === value) return "";
  return /^[A-Za-z]:$/.test(cut) ? `${cut}\\` : cut;
}

export function PathPicker({
  value,
  onChange,
  placeholder,
  label,
  mode = "dir",
  id,
  describedBy,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label: string;
  mode?: PickMode;
  id?: string;
  describedBy?: string;
}) {
  const [browsing, setBrowsing] = useState(false);
  return (
    <>
      <TextInput
        id={id}
        style={{ flex: 1 }}
        ff="monospace"
        aria-label={label}
        aria-describedby={describedBy}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
      <Button variant="default" onClick={() => setBrowsing(true)}>
        浏览
      </Button>
      {browsing && (
        <DirectoryBrowser
          initialPath={startFolder(value.trim(), mode)}
          mode={mode}
          onSelect={(path) => {
            onChange(path);
            setBrowsing(false);
          }}
          onClose={() => setBrowsing(false)}
        />
      )}
    </>
  );
}

function DirectoryBrowser({
  initialPath,
  mode,
  onSelect,
  onClose,
}: {
  initialPath: string;
  mode: PickMode;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [typed, setTyped] = useState(initialPath);
  const pickFile = mode === "file";
  const listing = useQuery({
    queryKey: ["dirs", path, mode],
    queryFn: () => fetchDirs(path, pickFile),
    retry: false,
  });

  const go = (next: string) => {
    setPath(next);
    setTyped(next);
  };
  const parent = listing.data?.parent;
  const files = pickFile ? (listing.data?.files ?? []) : [];

  return (
    <Modal opened onClose={onClose} title={pickFile ? "选择文件" : "选择目录"} size="lg">
      <Stack>
        <Group gap="xs" wrap="nowrap">
          <TextInput
            style={{ flex: 1 }}
            ff="monospace"
            aria-label="当前目录"
            placeholder="此电脑"
            value={typed}
            onChange={(event) => setTyped(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") go(typed.trim());
            }}
          />
          <Button
            variant="default"
            disabled={parent === null || parent === undefined}
            onClick={() => go(parent ?? "")}
          >
            上一级
          </Button>
        </Group>
        <ScrollArea h={360} type="auto">
          {listing.isPending && <Loader size="sm" />}
          {listing.isError && (
            <Alert color="red" title="打不开这个目录">
              {listing.error.message}
            </Alert>
          )}
          {listing.data?.dirs.length === 0 && files.length === 0 && (
            <Text size="sm" c="dimmed">
              {pickFile ? "这里没有子目录和文件" : "这里没有子目录"}
            </Text>
          )}
          {listing.data?.dirs.map((dir) => (
            <NavLink key={dir.path} label={dir.name} onClick={() => go(dir.path)} />
          ))}
          {files.map((file) => (
            <NavLink
              key={file.path}
              label={file.name}
              aria-label={`选择文件 ${file.name}`}
              c="var(--mo-dim)"
              onClick={() => onSelect(file.path)}
            />
          ))}
        </ScrollArea>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          {!pickFile && (
            <Button
              disabled={!listing.isSuccess || listing.data.path === ""}
              onClick={() => listing.data && onSelect(listing.data.path)}
            >
              选择这个目录
            </Button>
          )}
        </Group>
      </Stack>
    </Modal>
  );
}
