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
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  type NetworkConfig,
  type SecretKey,
  type SecretsUpdate,
  checkTmdb,
  fetchSecretsStatus,
  fetchSettings,
  fetchSystemInfo,
  saveSecrets,
  saveSettingsSection,
} from "../api";

export function SettingsPage() {
  return (
    <Stack maw={640}>
      <Title order={2}>设置</Title>
      <NetworkSection />
      <TmdbSection />
      <AboutSection />
    </Stack>
  );
}

function NetworkSection() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const [network, setDraft] = useState<NetworkConfig | null>(null);
  const [port, setPort] = useState<number | "">("");
  useEffect(() => {
    if (settings.data !== undefined) {
      setDraft(settings.data.network);
      setPort(settings.data.network.proxy_port);
    }
  }, [settings.data]);
  const save = useMutation({
    mutationFn: (next: NetworkConfig) => saveSettingsSection("network", next),
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      notifications.show({ color: "green", message: "网络设置已保存" });
    },
  });

  if (settings.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {settings.error.message}
      </Alert>
    );
  }
  if (network === null) {
    return <Text c="dimmed">正在读取设置…</Text>;
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
        {save.isError && (
          <Alert color="red" title="保存失败">
            {save.error.message}
          </Alert>
        )}
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

function SecretField({
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

function TmdbSection() {
  const check = useMutation({ mutationFn: checkTmdb });
  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>TMDB</Title>
        <Text size="sm" c="dimmed">
          电影的元数据和图片来自 TMDB。可以填 API Key（v3）或读访问令牌（v4）。测试连接使用已保存的网络设置。
        </Text>
        <SecretField
          secretKey="tmdb_api_key"
          label="TMDB API Key"
          onChanged={() => check.reset()}
        />
        <Group>
          <Button variant="default" loading={check.isPending} onClick={() => check.mutate()}>
            测试连接
          </Button>
        </Group>
        {check.isError && (
          <Alert color="red" title="测试失败">
            {check.error.message}
          </Alert>
        )}
        {check.data !== undefined && (
          <Alert
            color={check.data.ok ? "green" : "red"}
            title={check.data.ok ? "连接成功" : "连接失败"}
          >
            {check.data.message}
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}

function AboutSection() {
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
