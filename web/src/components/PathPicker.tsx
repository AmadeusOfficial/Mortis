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

export function PathPicker({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label: string;
}) {
  const [browsing, setBrowsing] = useState(false);
  return (
    <>
      <TextInput
        style={{ flex: 1 }}
        ff="monospace"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
      <Button variant="default" onClick={() => setBrowsing(true)}>
        浏览
      </Button>
      {browsing && (
        <DirectoryBrowser
          initialPath={value.trim()}
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
  onSelect,
  onClose,
}: {
  initialPath: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [typed, setTyped] = useState(initialPath);
  const listing = useQuery({
    queryKey: ["dirs", path],
    queryFn: () => fetchDirs(path),
    retry: false,
  });

  const go = (next: string) => {
    setPath(next);
    setTyped(next);
  };
  const parent = listing.data?.parent;

  return (
    <Modal opened onClose={onClose} title="选择目录" size="lg">
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
          {listing.data?.dirs.length === 0 && (
            <Text size="sm" c="dimmed">
              这里没有子目录
            </Text>
          )}
          {listing.data?.dirs.map((dir) => (
            <NavLink key={dir.path} label={dir.name} onClick={() => go(dir.path)} />
          ))}
        </ScrollArea>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={!listing.isSuccess || listing.data.path === ""}
            onClick={() => listing.data && onSelect(listing.data.path)}
          >
            选择这个目录
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
