import { Alert, Button, Checkbox, Code, Group, Modal, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type PackagePreview,
  type PluginCapability,
  confirmPluginPackage,
  uninstallPlugin,
} from "../api";
import { Pill } from "./ui";

export const TRUST_WARNING =
  "导入的插件是在本机运行的 Python 代码，现有检查只是约定检查，不是沙箱，只导入你信任的插件";

const CAPABILITY_LABELS: Record<PluginCapability, string> = {
  data_type: "数据类型",
  source: "来源",
  player: "播放",
  hook: "事件钩子",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function invalidatePluginQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["plugins"] });
  void queryClient.invalidateQueries({ queryKey: ["data-types"] });
  void queryClient.invalidateQueries({ queryKey: ["libraries"] });
  void queryClient.invalidateQueries({ queryKey: ["source-orders"] });
}

// 上传检查通过后的预览弹窗：文件清单和哈希、插件提供的内容、订阅事件和权限、覆盖时的新旧对比。
// 勾选「我信任这个插件」之前不能确认；确认之后后端才会试运行插件代码。
export function PluginImportModal({
  preview,
  onClose,
}: {
  preview: PackagePreview;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [trusted, setTrusted] = useState(false);
  const existing = preview.existing;
  const permissions = [...new Set(preview.hooks.flatMap((hook) => hook.writes))];
  const install = useMutation({
    mutationFn: () => confirmPluginPackage(preview.token, trusted),
    onSuccess: (done) => {
      invalidatePluginQueries(queryClient);
      notifications.show({
        color: done.needs_restart ? "yellow" : "green",
        title: done.replaced ? `已覆盖 ${done.name}` : `已安装 ${done.name}`,
        message: done.needs_restart
          ? "覆盖升级要重启莫提斯才完全生效。"
          : `${done.version} 已加载。`,
        autoClose: done.needs_restart ? false : 4000,
      });
      onClose();
    },
  });
  const sources = preview.sources.map((source) => source.name);
  const players = preview.players.map((player) => player.name);

  return (
    <Modal opened onClose={install.isPending ? () => undefined : onClose} title="导入插件" size="xl">
      <Stack gap="md">
        <Alert color="red" title="先读这个">
          {TRUST_WARNING}
        </Alert>
        <Stack gap={4}>
          <Group gap="xs" wrap="wrap">
            <Text fw={600}>{preview.name}</Text>
            <Pill tone="neutral">
              <span className="mo-mono">{preview.version}</span>
            </Pill>
            {preview.capabilities.map((capability) => (
              <Pill key={capability} tone={capability === "source" ? "accent" : "neutral"}>
                {CAPABILITY_LABELS[capability]}
              </Pill>
            ))}
          </Group>
          <Text size="xs" c="var(--mo-muted)">
            id：<span className="mo-mono">{preview.id}</span>　接口版本 {preview.api_version}
            {preview.min_mortis_version !== null && `　需要莫提斯 ${preview.min_mortis_version} 或更高`}
          </Text>
          {sources.length > 0 && (
            <Text size="xs" c="var(--mo-muted)">
              带来源：{sources.join("、")}
            </Text>
          )}
          {players.length > 0 && (
            <Text size="xs" c="var(--mo-muted)">
              带播放：{players.join("、")}
            </Text>
          )}
          {preview.hooks.length > 0 && (
            <Text size="xs" c="var(--mo-muted)">
              订阅事件：{preview.hooks.map((hook) => hook.event).join("、")}
            </Text>
          )}
          <Text size="xs" c="var(--mo-muted)">
            权限声明（可写）：{permissions.length > 0 ? permissions.join("、") : "无"}
          </Text>
        </Stack>

        {existing !== null && (
          <Alert color="yellow" title="已装有这个插件，将覆盖">
            <Stack gap={4}>
              <Text size="sm">
                旧版本 <span className="mo-mono">{existing.version || "未知"}</span> → 新版本{" "}
                <span className="mo-mono">{preview.version}</span>
              </Text>
              <Text size="xs">
                新增 {existing.added.length} 个、删除 {existing.removed.length} 个、改动{" "}
                {existing.changed.length} 个文件
                {existing.package_hash === preview.package_hash && "（内容完全相同）"}。
              </Text>
              {[...existing.added.map((p) => `＋ ${p}`), ...existing.removed.map((p) => `－ ${p}`), ...existing.changed.map((p) => `～ ${p}`)]
                .slice(0, 20)
                .map((line) => (
                  <Text key={line} size="xs" className="mo-mono">
                    {line}
                  </Text>
                ))}
              <Text size="sm" fw={600}>
                覆盖升级要重启莫提斯才完全生效。
              </Text>
            </Stack>
          </Alert>
        )}

        <div style={{ maxHeight: 260, overflow: "auto" }}>
          <Table withTableBorder fz="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>文件</Table.Th>
                <Table.Th>大小</Table.Th>
                <Table.Th>SHA-256</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {preview.files.map((file) => (
                <Table.Tr key={file.path}>
                  <Table.Td className="mo-mono">{file.path}</Table.Td>
                  <Table.Td>{formatSize(file.size)}</Table.Td>
                  <Table.Td>
                    <span className="mo-mono" title={file.sha256}>
                      {file.sha256.slice(0, 16)}…
                    </span>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
        <Text size="xs" c="var(--mo-muted)">
          共 {preview.files.length} 个文件，{formatSize(preview.total_size)}。包哈希：
          <Code>{preview.package_hash}</Code>
        </Text>
        <Text size="xs" c="var(--mo-muted)">
          点「确认导入」后，莫提斯才会试运行这个插件（检查并真的 import
          一遍），这一步会执行插件代码，不是安全检查。
        </Text>

        <Checkbox
          checked={trusted}
          onChange={(event) => setTrusted(event.currentTarget.checked)}
          label="我信任这个插件"
        />
        {install.isError && (
          <Alert color="red" title="没有导入">
            {install.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={install.isPending}>
            取消
          </Button>
          <Button
            color={existing !== null ? "yellow" : undefined}
            disabled={!trusted}
            loading={install.isPending}
            onClick={() => install.mutate()}
          >
            {existing !== null ? "确认覆盖" : "确认导入"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// 卸载确认：只删插件文件夹，插件自己的设置和数据保留。
export function UninstallModal({
  id,
  name,
  onClose,
}: {
  id: string;
  name: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => uninstallPlugin(id),
    onSuccess: () => {
      invalidatePluginQueries(queryClient);
      notifications.show({ color: "green", message: `已卸载 ${name}` });
      onClose();
    },
  });
  return (
    <Modal opened onClose={remove.isPending ? () => undefined : onClose} title={`卸载 ${name}`}>
      <Stack>
        <Text size="sm">
          会删除这个插件的文件夹，它带的来源、播放和钩子一起消失。已有的条目、插件的设置和它自己的数据会保留，以后重装还能接上。
        </Text>
        {remove.isError && (
          <Alert color="red" title="没有卸载">
            {remove.error.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={remove.isPending}>
            取消
          </Button>
          <Button color="red" loading={remove.isPending} onClick={() => remove.mutate()}>
            卸载
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
