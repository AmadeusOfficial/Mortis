import {
  Alert,
  Button,
  Group,
  NumberInput,
  PasswordInput,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
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
import styles from "../../pages/SettingsPage.module.css";
import { SaveBar, SettingCard, SettingRow } from "./SettingLayout";

function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

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
  const saved = settings.data?.[name] ?? null;
  return { saved, draft, setDraft, save, settings };
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
  const { saved, draft: network, setDraft, save, settings } = useSection("network", "网络设置");
  const [port, setPort] = useState<number | "">("");
  const hostId = useId();
  const hostHintId = useId();
  const portId = useId();
  const portHintId = useId();
  const certId = useId();
  const certHintId = useId();

  const loadedPort = network?.proxy_port;
  useEffect(() => {
    if (loadedPort !== undefined) {
      setPort(loadedPort);
    }
  }, [loadedPort]);

  if (settings.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {settings.error.message}
      </Alert>
    );
  }
  if (network === null || saved === null) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }

  const portInvalid = port === "" || port < 1 || port > 65535;
  const setNetwork = (patch: Partial<NetworkConfig>) => setDraft({ ...network, ...patch });

  let dirtyCount = 0;
  if (network.proxy_host !== saved.proxy_host) dirtyCount += 1;
  if (port !== saved.proxy_port) dirtyCount += 1;
  if (network.ignore_cert_errors !== saved.ignore_cert_errors) dirtyCount += 1;

  const discard = () => {
    setDraft(saved);
    setPort(saved.proxy_port);
    save.reset();
  };

  return (
    <Stack gap="md">
      <SettingCard title="网络" description="配置代理服务器与 HTTPS 证书校验行为">
        <div className={styles.fieldList}>
          <SettingRow
            label="代理主机"
            hint="留空表示不用代理"
            htmlFor={hostId}
            hintId={hostHintId}
          >
            <TextInput
              id={hostId}
              aria-describedby={hostHintId}
              className={styles.monoInput}
              maw={340}
              value={network.proxy_host}
              onChange={(event) => setNetwork({ proxy_host: event.currentTarget.value })}
            />
          </SettingRow>
          <SettingRow
            label="代理端口"
            hint="1 到 65535 之间的端口号"
            htmlFor={portId}
            hintId={portHintId}
          >
            <NumberInput
              id={portId}
              aria-describedby={portHintId}
              className={styles.monoInput}
              maw={160}
              min={1}
              max={65535}
              allowDecimal={false}
              value={port}
              onChange={(value) => setPort(typeof value === "number" ? value : "")}
              error={portInvalid ? "代理端口要在 1 到 65535 之间" : undefined}
            />
          </SettingRow>
          <SettingRow
            label="忽略证书错误"
            hint="使用自签名或不受信任代理证书时开启"
            htmlFor={certId}
            hintId={certHintId}
          >
            <Switch
              id={certId}
              aria-describedby={certHintId}
              checked={network.ignore_cert_errors}
              onChange={(event) => setNetwork({ ignore_cert_errors: event.currentTarget.checked })}
            />
          </SettingRow>
        </div>
      </SettingCard>

      <SettingCard
        title="代理认证"
        description="需要身份验证的代理服务器凭据，单独保存或清除"
      >
        <div className={styles.fieldList}>
          <SecretField secretKey="proxy_username" label="代理用户名" />
          <SecretField secretKey="proxy_password" label="代理密码" />
        </div>
      </SettingCard>

      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel="保存网络设置"
        saving={save.isPending}
        disabled={portInvalid}
        error={save.error}
        onDiscard={discard}
        onSave={() => {
          if (port !== "") {
            save.mutate({ ...network, proxy_port: port });
          }
        }}
      />
    </Stack>
  );
}

export function SecretField({
  secretKey,
  label,
  hintExtra,
  onChanged,
}: {
  secretKey: SecretKey;
  label: string;
  hintExtra?: string;
  onChanged?: () => void;
}) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus });

  const save = useMutation({
    mutationFn: (next: string | null) => {
      const update: SecretsUpdate = {};
      update[secretKey] = next;
      return saveSecrets(update);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(["secrets"], saved);
      onChanged?.();
    },
  });

  return (
    <SecretInputRow
      label={label}
      hintExtra={hintExtra}
      isSet={status.data === undefined ? undefined : status.data[secretKey]}
      loadError={status.isError ? status.error : null}
      saving={save.isPending}
      error={save.error}
      onSave={(next, done) => save.mutate(next, { onSuccess: done })}
    />
  );
}

// 密钥输入行：只显示是否已填，保存后清空输入框；清除时 next 是 null。
export function SecretInputRow({
  label,
  hintExtra,
  isSet,
  loadError,
  saving,
  error,
  onSave,
}: {
  label: string;
  hintExtra?: string;
  // undefined 表示还在读取。
  isSet: boolean | undefined;
  loadError: Error | null;
  saving: boolean;
  error: Error | null;
  onSave: (next: string | null, done: () => void) => void;
}) {
  const [value, setValue] = useState("");
  const inputId = useId();
  const hintId = useId();

  const known = isSet !== undefined;
  const statusText =
    loadError !== null
      ? `读取失败：${loadError.message}`
      : !known
        ? "正在读取…"
        : isSet
          ? "已填写。输入新值会覆盖，留空不修改。"
          : "未填写";
  const hint = hintExtra ? `${statusText} ${hintExtra}` : statusText;
  const submit = (next: string | null) =>
    onSave(next, () => {
      setValue("");
      notifications.show({
        color: "green",
        message: next === null ? `${label}已清除` : `${label}已保存`,
      });
    });

  return (
    <SettingRow label={label} hint={hint} htmlFor={inputId} hintId={hintId} align="start">
      <Stack gap={6}>
        <Group align="center" gap="xs" wrap="nowrap">
          <PasswordInput
            id={inputId}
            aria-describedby={hintId}
            style={{ flex: 1 }}
            autoComplete="new-password"
            value={value}
            onChange={(event) => setValue(event.currentTarget.value)}
          />
          <Button
            variant="default"
            aria-label={`保存${label}`}
            disabled={!known || value.trim() === ""}
            loading={saving}
            onClick={() => submit(value)}
          >
            保存
          </Button>
          <Button
            variant="subtle"
            color="red"
            aria-label={`清除${label}`}
            disabled={!known || !isSet || saving}
            onClick={() => submit(null)}
          >
            清除
          </Button>
        </Group>
        {error !== null && (
          <Text c="var(--mo-bad)" size="xs">
            {error.message}
          </Text>
        )}
      </Stack>
    </SettingRow>
  );
}

function splitWords(text: string): string[] {
  return text
    .split(/[\s,，]+/)
    .map((word) => word.trim())
    .filter((word) => word !== "");
}

export function FilesSection() {
  const { saved, draft: files, save, settings } = useSection("files", "文件设置");
  const [videoText, setVideoText] = useState<string | null>(null);
  const [subtitleText, setSubtitleText] = useState<string | null>(null);
  const videoId = useId();
  const videoHintId = useId();
  const subId = useId();
  const subHintId = useId();

  if (settings.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {settings.error.message}
      </Alert>
    );
  }
  if (files === null || saved === null) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }

  const video = videoText ?? files.video_extensions.join(" ");
  const subtitle = subtitleText ?? files.subtitle_extensions.join(" ");
  const description = "扫描时只认这些扩展名，用空格或换行分隔";

  const videoChanged = !arraysEqual(splitWords(video), saved.video_extensions);
  const subtitleChanged = !arraysEqual(splitWords(subtitle), saved.subtitle_extensions);
  const dirtyCount = (videoChanged ? 1 : 0) + (subtitleChanged ? 1 : 0);

  const discard = () => {
    setVideoText(null);
    setSubtitleText(null);
    save.reset();
  };

  return (
    <Stack gap="md">
      <SettingCard title="文件" description={description}>
        <div className={styles.fieldList}>
          <SettingRow
            label="视频扩展名"
            hint="例如 mp4 mkv avi"
            htmlFor={videoId}
            hintId={videoHintId}
            align="start"
          >
            <Textarea
              id={videoId}
              aria-describedby={videoHintId}
              className={styles.monoInput}
              autosize
              minRows={2}
              value={video}
              onChange={(event) => setVideoText(event.currentTarget.value)}
            />
          </SettingRow>
          <SettingRow
            label="字幕扩展名"
            hint="例如 srt ass ssa vtt"
            htmlFor={subId}
            hintId={subHintId}
            align="start"
          >
            <Textarea
              id={subId}
              aria-describedby={subHintId}
              className={styles.monoInput}
              autosize
              minRows={1}
              value={subtitle}
              onChange={(event) => setSubtitleText(event.currentTarget.value)}
            />
          </SettingRow>
        </div>
      </SettingCard>

      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel="保存文件设置"
        saving={save.isPending}
        error={save.error}
        onDiscard={discard}
        onSave={() => {
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
      />
    </Stack>
  );
}

export function TasksSection() {
  const { saved, draft: tasks, setDraft, save, settings } = useSection("tasks", "任务设置");
  const [concurrency, setConcurrency] = useState<number | "">("");
  const [keepDays, setKeepDays] = useState<number | "">("");
  const concId = useId();
  const concHintId = useId();
  const keepId = useId();
  const keepHintId = useId();

  const loadedConcurrency = tasks?.concurrency;
  const loadedKeepDays = tasks?.keep_days;
  useEffect(() => {
    if (loadedConcurrency !== undefined) {
      setConcurrency(loadedConcurrency);
    }
  }, [loadedConcurrency]);
  useEffect(() => {
    if (loadedKeepDays !== undefined) {
      setKeepDays(loadedKeepDays);
    }
  }, [loadedKeepDays]);

  if (settings.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {settings.error.message}
      </Alert>
    );
  }
  if (tasks === null || saved === null) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }

  const concInvalid = concurrency === "" || concurrency < 1 || concurrency > 8;
  const keepInvalid = keepDays === "" || keepDays < 1 || keepDays > 3650;

  let dirtyCount = 0;
  if (concurrency !== saved.concurrency) dirtyCount += 1;
  if (keepDays !== saved.keep_days) dirtyCount += 1;

  const discard = () => {
    setDraft(saved);
    setConcurrency(saved.concurrency);
    setKeepDays(saved.keep_days);
    save.reset();
  };

  return (
    <Stack gap="md">
      <SettingCard title="任务" description="控制后台任务并发数与历史记录保留时间">
        <div className={styles.fieldList}>
          <SettingRow
            label="同时运行的任务数"
            hint="改了以后重启莫提斯生效"
            htmlFor={concId}
            hintId={concHintId}
          >
            <NumberInput
              id={concId}
              aria-describedby={concHintId}
              className={styles.monoInput}
              maw={160}
              min={1}
              max={8}
              allowDecimal={false}
              value={concurrency}
              onChange={(value) => {
                setConcurrency(typeof value === "number" ? value : "");
                if (typeof value === "number") {
                  setDraft({ ...tasks, concurrency: value });
                }
              }}
              error={concInvalid ? "要在 1 到 8 之间" : undefined}
            />
          </SettingRow>
          <SettingRow
            label="已结束的任务保留天数"
            hint="1 到 3650 天"
            htmlFor={keepId}
            hintId={keepHintId}
          >
            <NumberInput
              id={keepId}
              aria-describedby={keepHintId}
              className={styles.monoInput}
              maw={160}
              min={1}
              max={3650}
              allowDecimal={false}
              value={keepDays}
              onChange={(value) => {
                setKeepDays(typeof value === "number" ? value : "");
                if (typeof value === "number") {
                  setDraft({ ...tasks, keep_days: value });
                }
              }}
              error={keepInvalid ? "要在 1 到 3650 之间" : undefined}
            />
          </SettingRow>
        </div>
      </SettingCard>

      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel="保存任务设置"
        saving={save.isPending}
        disabled={concInvalid || keepInvalid}
        error={save.error}
        onDiscard={discard}
        onSave={() => {
          if (concurrency !== "" && keepDays !== "") {
            save.mutate({ concurrency, keep_days: keepDays });
          }
        }}
      />
    </Stack>
  );
}

export function AboutSection() {
  const info = useQuery({ queryKey: ["system-info"], queryFn: fetchSystemInfo });
  return (
    <SettingCard title="关于" description="莫提斯版本、数据目录与配置文件位置">
      {info.isError && (
        <div className={styles.cardBody}>
          <Alert color="red" title="读取失败">
            {info.error.message}
          </Alert>
        </div>
      )}
      {info.isPending && (
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            正在读取…
          </Text>
        </div>
      )}
      {info.isSuccess && (
        <div className={styles.fieldList}>
          <SettingRow label="版本">
            <Text size="sm" className="mo-mono">
              {info.data.version}
            </Text>
          </SettingRow>
          <SettingRow label="数据目录">
            <Text size="sm" className="mo-mono" style={{ wordBreak: "break-all" }}>
              {info.data.data_dir}
            </Text>
          </SettingRow>
          <SettingRow label="配置文件">
            <Text size="sm" c="var(--mo-dim)">
              设置保存在数据目录下的 config.toml，密钥保存在 secrets.toml。
            </Text>
          </SettingRow>
        </div>
      )}
    </SettingCard>
  );
}
