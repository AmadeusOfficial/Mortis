import {
  Alert,
  Button,
  Group,
  NumberInput,
  Paper,
  PasswordInput,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  type AppConfig,
  type NetworkConfig,
  type SecretKey,
  type SecretsUpdate,
  type SettingsSection,
  fetchSecretsStatus,
  fetchSettings,
  fetchSystemInfo,
  saveSecrets,
  saveSettingsSection,
} from "../../api";

// 通用设置的一节：网络、文件、任务。保存后整份设置覆盖本地缓存。
export function useSection<K extends SettingsSection>(name: K, label: string) {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const [draft, setDraft] = useState<AppConfig[K] | null>(null);
  useEffect(() => {
    // 只在还没有草稿时取一次；设置重新拉取不能冲掉正在改的内容。
    if (settings.data !== undefined && draft === null) {
      setDraft(settings.data[name]);
    }
  }, [settings.data, draft, name]);
  const save = useMutation({
    mutationFn: (value: AppConfig[K]) => saveSettingsSection(name, value),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      setDraft(saved[name]);
      notifications.show({ color: "green", message: `${label}已保存` });
    },
  });
  return { draft, setDraft, save };
}

export function SaveError({ error }: { error: Error | null }) {
  if (error === null) {
    return null;
  }
  return (
    <Alert color="red" title="保存失败">
      {error.message}
    </Alert>
  );
}

export function NetworkSection() {
  const { draft: network, setDraft, save } = useSection("network", "网络设置");
  const [port, setPort] = useState<number | "">("");
  const loadedPort = network?.proxy_port;
  useEffect(() => {
    if (loadedPort !== undefined) {
      setPort(loadedPort);
    }
  }, [loadedPort]);

  if (network === null) {
    return null;
  }
  const portInvalid = port === "" || port < 1 || port > 65535;
  const setNetwork = (patch: Partial<NetworkConfig>) => setDraft({ ...network, ...patch });

  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>网络</Title>
        <Group grow align="flex-start">
          <TextInput
            label="代理主机"
            description="留空表示不用代理"
            value={network.proxy_host}
            onChange={(event) => setNetwork({ proxy_host: event.currentTarget.value })}
          />
          <NumberInput
            label="代理端口"
            min={1}
            max={65535}
            allowDecimal={false}
            value={port}
            onChange={(value) => setPort(typeof value === "number" ? value : "")}
            error={portInvalid ? "代理端口要在 1 到 65535 之间" : undefined}
          />
        </Group>
        <Switch
          label="忽略证书错误"
          checked={network.ignore_cert_errors}
          onChange={(event) => setNetwork({ ignore_cert_errors: event.currentTarget.checked })}
        />
        <SaveError error={save.error} />
        <Group>
          <Button
            loading={save.isPending}
            disabled={portInvalid}
            onClick={() => {
              if (port !== "") {
                save.mutate({ ...network, proxy_port: port });
              }
            }}
          >
            保存网络设置
          </Button>
        </Group>
        <SecretField secretKey="proxy_username" label="代理用户名" />
        <SecretField secretKey="proxy_password" label="代理密码" />
      </Stack>
    </Paper>
  );
}

export function SecretField({
  secretKey,
  label,
  onChanged,
}: {
  secretKey: SecretKey;
  label: string;
  onChanged?: () => void;
}) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus });
  const [value, setValue] = useState("");
  const save = useMutation({
    mutationFn: (next: string | null) => {
      const update: SecretsUpdate = {};
      update[secretKey] = next;
      return saveSecrets(update);
    },
    onSuccess: (saved, next) => {
      queryClient.setQueryData(["secrets"], saved);
      setValue("");
      onChanged?.();
      notifications.show({
        color: "green",
        message: next === null ? `${label}已清除` : `${label}已保存`,
      });
    },
  });
  const known = status.data !== undefined;
  const isSet = status.data?.[secretKey] ?? false;

  return (
    <Stack gap={4}>
      <Group align="flex-end" wrap="nowrap">
        <PasswordInput
          style={{ flex: 1 }}
          label={label}
          description={
            status.isError
              ? `读取失败：${status.error.message}`
              : !known
                ? "正在读取…"
                : isSet
                  ? "已填写。输入新值会覆盖，留空不修改。"
                  : "未填写"
          }
          autoComplete="new-password"
          value={value}
          onChange={(event) => setValue(event.currentTarget.value)}
        />
        <Button
          variant="light"
          aria-label={`保存${label}`}
          disabled={!known || value.trim() === ""}
          loading={save.isPending}
          onClick={() => save.mutate(value)}
        >
          保存
        </Button>
        <Button
          variant="subtle"
          color="red"
          aria-label={`清除${label}`}
          disabled={!known || !isSet}
          onClick={() => save.mutate(null)}
        >
          清除
        </Button>
      </Group>
      {save.isError && (
        <Text c="red" size="sm">
          {save.error.message}
        </Text>
      )}
    </Stack>
  );
}

function splitWords(text: string): string[] {
  return text
    .split(/[\s,，]+/)
    .map((word) => word.trim())
    .filter((word) => word !== "");
}

export function FilesSection() {
  const { draft: files, save } = useSection("files", "文件设置");
  const [videoText, setVideoText] = useState<string | null>(null);
  const [subtitleText, setSubtitleText] = useState<string | null>(null);
  if (files === null) {
    return null;
  }
  const video = videoText ?? files.video_extensions.join(" ");
  const subtitle = subtitleText ?? files.subtitle_extensions.join(" ");
  const description = "扫描时只认这些扩展名，用空格或换行分隔";

  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>文件</Title>
        <Textarea
          label="视频扩展名"
          description={description}
          autosize
          minRows={2}
          value={video}
          onChange={(event) => setVideoText(event.currentTarget.value)}
        />
        <Textarea
          label="字幕扩展名"
          description={description}
          autosize
          minRows={1}
          value={subtitle}
          onChange={(event) => setSubtitleText(event.currentTarget.value)}
        />
        <SaveError error={save.error} />
        <Group>
          <Button
            loading={save.isPending}
            onClick={() => {
              save.mutate(
                { video_extensions: splitWords(video), subtitle_extensions: splitWords(subtitle) },
                {
                  onSuccess: () => {
                    setVideoText(null);
                    setSubtitleText(null);
                  },
                },
              );
            }}
          >
            保存文件设置
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

export function TasksSection() {
  const { draft: tasks, setDraft, save } = useSection("tasks", "任务设置");
  if (tasks === null) {
    return null;
  }
  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>任务</Title>
        <Group grow align="flex-start">
          <NumberInput
            label="同时运行的任务数"
            description="改了以后重启莫提斯生效"
            min={1}
            max={8}
            allowDecimal={false}
            value={tasks.concurrency}
            onChange={(value) =>
              typeof value === "number" && setDraft({ ...tasks, concurrency: value })
            }
          />
          <NumberInput
            label="已结束的任务保留天数"
            min={1}
            max={3650}
            allowDecimal={false}
            value={tasks.keep_days}
            onChange={(value) =>
              typeof value === "number" && setDraft({ ...tasks, keep_days: value })
            }
          />
        </Group>
        <SaveError error={save.error} />
        <Group>
          <Button loading={save.isPending} onClick={() => save.mutate(tasks)}>
            保存任务设置
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

export function AboutSection() {
  const info = useQuery({ queryKey: ["system-info"], queryFn: fetchSystemInfo });
  return (
    <Paper withBorder p="md">
      <Stack gap="xs">
        <Title order={4}>关于</Title>
        {info.isError && (
          <Text size="sm" c="red">
            读取失败：{info.error.message}
          </Text>
        )}
        {info.isSuccess && (
          <>
            <Text size="sm">版本 {info.data.version}</Text>
            <Text size="sm">数据目录 {info.data.data_dir}</Text>
            <Text size="sm" c="dimmed">
              设置保存在数据目录下的 config.toml，密钥保存在 secrets.toml。
            </Text>
          </>
        )}
      </Stack>
    </Paper>
  );
}
