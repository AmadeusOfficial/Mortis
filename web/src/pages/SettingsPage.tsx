import {
  Alert,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  type AppConfig,
  type MovieImages,
  type MovieOutputConfig,
  type MovieScrapeConfig,
  type NetworkConfig,
  type SecretKey,
  type SecretsUpdate,
  type SettingsSection,
  checkTmdb,
  fetchSecretsStatus,
  fetchSettings,
  fetchSystemInfo,
  saveSecrets,
  saveSettingsSection,
} from "../api";

export function SettingsPage() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  return (
    <Stack maw={640}>
      <Title order={2}>设置</Title>
      {settings.isError && (
        <Alert color="red" title="读不到设置">
          {settings.error.message}
        </Alert>
      )}
      {settings.isPending && <Text c="dimmed">正在读取设置…</Text>}
      {settings.isSuccess && (
        <>
          <NetworkSection />
          <TmdbSection />
          <MovieSection />
          <FilesSection />
          <TasksSection />
        </>
      )}
      <AboutSection />
    </Stack>
  );
}

function useSection<K extends SettingsSection>(name: K, label: string) {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const [draft, setDraft] = useState<AppConfig[K] | null>(null);
  useEffect(() => {
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

function SaveError({ error }: { error: Error | null }) {
  if (error === null) {
    return null;
  }
  return (
    <Alert color="red" title="保存失败">
      {error.message}
    </Alert>
  );
}

function NetworkSection() {
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

const LANGUAGES = [
  { value: "zh-CN", label: "简体中文" },
  { value: "zh-TW", label: "繁体中文（台湾）" },
  { value: "zh-HK", label: "繁体中文（香港）" },
  { value: "en-US", label: "英语" },
  { value: "ja-JP", label: "日语" },
  { value: "ko-KR", label: "韩语" },
];
const POSTER_SIZES = [
  { value: "w342", label: "小（w342）" },
  { value: "w500", label: "中（w500）" },
  { value: "w780", label: "大（w780）" },
  { value: "original", label: "原图" },
];
const FANART_SIZES = [
  { value: "w780", label: "小（w780）" },
  { value: "w1280", label: "中（w1280）" },
  { value: "original", label: "大（原图）" },
];
const IMAGE_LABELS: [keyof MovieImages, string][] = [
  ["poster", "海报"],
  ["fanart", "同人画"],
  ["clearlogo", "透明标志"],
  ["keyart", "关键艺术图"],
  ["collection_poster", "系列海报"],
  ["collection_fanart", "系列同人画"],
  ["actor_images", "演员头像"],
];

const TEMPLATE_HINT =
  "可用 ${title} ${originalTitle} ${year} ${edition} ${videoFormat} ${audioCodec} ${tmdb} ${imdb}；${前缀,标记,后缀} 只在标记有值时出现；给 Plex 用可在目录模板末尾加 ${{tmdb-,tmdb,}}";

function splitWords(text: string): string[] {
  return text
    .split(/[\s,，]+/)
    .map((word) => word.trim())
    .filter((word) => word !== "");
}

function MovieSection() {
  const { draft: movie, setDraft, save } = useSection("movie", "电影刮削设置");
  const [skipText, setSkipText] = useState<string | null>(null);
  if (movie === null) {
    return null;
  }
  const set = (patch: Partial<MovieScrapeConfig>) => setDraft({ ...movie, ...patch });
  const setOutput = (patch: Partial<MovieOutputConfig>) =>
    set({ output: { ...movie.output, ...patch } });
  const skip = skipText ?? movie.skip_folders.join("\n");
  const country = movie.certification_country;
  const countryInvalid = !/^[A-Z]{2}$/.test(country);

  return (
    <Paper withBorder p="md">
      <Stack>
        <Title order={4}>电影刮削</Title>
        <Group grow align="flex-start">
          <Select
            label="刮削语言"
            data={LANGUAGES}
            allowDeselect={false}
            value={movie.language}
            onChange={(value) => value !== null && set({ language: value })}
          />
          <TextInput
            label="分级国家"
            description="TMDB 的国家代码，例如 US、CN"
            maxLength={2}
            value={country}
            error={countryInvalid ? "填两位字母" : undefined}
            onChange={(event) =>
              set({ certification_country: event.currentTarget.value.toUpperCase() })
            }
          />
        </Group>
        <NumberInput
          label="匹配阈值"
          description="文件名和 TMDB 结果的相似度达到这个值才自动认定"
          min={0}
          max={1}
          step={0.05}
          decimalScale={2}
          value={movie.match_threshold}
          onChange={(value) => typeof value === "number" && set({ match_threshold: value })}
        />
        <Switch
          label="包含成人内容"
          checked={movie.include_adult}
          onChange={(event) => set({ include_adult: event.currentTarget.checked })}
        />
        <Group grow align="flex-start">
          <Select
            label="海报尺寸"
            data={POSTER_SIZES}
            allowDeselect={false}
            value={movie.poster_size}
            onChange={(value) => value !== null && set({ poster_size: value })}
          />
          <Select
            label="同人画尺寸"
            data={FANART_SIZES}
            allowDeselect={false}
            value={movie.fanart_size}
            onChange={(value) => value !== null && set({ fanart_size: value })}
          />
        </Group>
        <Switch
          label="优先无文字的同人画"
          checked={movie.prefer_textless_fanart}
          onChange={(event) => set({ prefer_textless_fanart: event.currentTarget.checked })}
        />
        <Stack gap={6}>
          <Text size="sm" fw={500}>
            要抓的图片
          </Text>
          <Group gap="md">
            {IMAGE_LABELS.map(([key, label]) => (
              <Checkbox
                key={key}
                label={label}
                checked={movie.images[key]}
                onChange={(event) =>
                  set({ images: { ...movie.images, [key]: event.currentTarget.checked } })
                }
              />
            ))}
          </Group>
        </Stack>
        <Textarea
          label="跳过的目录"
          description="一行一个目录名，扫描时整个目录不看"
          autosize
          minRows={2}
          value={skip}
          onChange={(event) => setSkipText(event.currentTarget.value)}
        />
        <Stack gap={6}>
          <Text size="sm" fw={500}>
            整理
          </Text>
          <Text size="xs" c="dimmed">
            刮削只改莫提斯里的资料；整理才动磁盘，让 Emby、Plex、Kodi 能直接读到。
          </Text>
          <Switch
            label="刮削后自动整理"
            checked={movie.output.organize_after_scrape}
            onChange={(event) =>
              setOutput({ organize_after_scrape: event.currentTarget.checked })
            }
          />
          <Switch
            label="整理时重命名"
            description="按下面的模板改目录和文件名"
            checked={movie.output.organize_rename}
            onChange={(event) => setOutput({ organize_rename: event.currentTarget.checked })}
          />
          <Switch
            label="整理时写 NFO 和图片"
            description="写到视频旁边；已经写过的条目改名后总会重写"
            checked={movie.output.organize_write_files}
            onChange={(event) =>
              setOutput({ organize_write_files: event.currentTarget.checked })
            }
          />
          <Checkbox.Group
            label="NFO 文件名"
            value={movie.output.nfo_names}
            onChange={(value) =>
              setOutput({ nfo_names: value as ("movie" | "filename")[] })
            }
          >
            <Group gap="md" mt={4}>
              <Checkbox value="movie" label="movie.nfo" />
              <Checkbox value="filename" label="视频名.nfo" />
            </Group>
          </Checkbox.Group>
          <Select
            label="图片文件名"
            data={[
              { value: "filename", label: "视频名-poster.jpg" },
              { value: "plain", label: "poster.jpg" },
            ]}
            allowDeselect={false}
            value={movie.output.image_style}
            onChange={(value) =>
              value !== null && setOutput({ image_style: value as "filename" | "plain" })
            }
          />
          <Switch
            label="系列图写成 movieset-poster.jpg"
            checked={movie.output.collection_image_names}
            onChange={(event) =>
              setOutput({ collection_image_names: event.currentTarget.checked })
            }
          />
          <TextInput
            label="演员头像目录"
            description="只填一层目录名，放在视频旁边"
            value={movie.output.actor_images_folder}
            onChange={(event) => setOutput({ actor_images_folder: event.currentTarget.value })}
          />
          <TextInput
            label="目录模板"
            description={TEMPLATE_HINT}
            value={movie.output.folder_template}
            onChange={(event) => setOutput({ folder_template: event.currentTarget.value })}
          />
          <TextInput
            label="文件模板"
            description={TEMPLATE_HINT}
            value={movie.output.file_template}
            onChange={(event) => setOutput({ file_template: event.currentTarget.value })}
          />
          <Group grow align="flex-start">
            <TextInput
              label="目录名空格换成"
              placeholder="不换"
              maxLength={3}
              value={movie.output.folder_space}
              onChange={(event) => setOutput({ folder_space: event.currentTarget.value })}
            />
            <TextInput
              label="文件名空格换成"
              maxLength={3}
              value={movie.output.file_space}
              onChange={(event) => setOutput({ file_space: event.currentTarget.value })}
            />
            <TextInput
              label="冒号换成"
              maxLength={3}
              value={movie.output.colon_replacement}
              onChange={(event) => setOutput({ colon_replacement: event.currentTarget.value })}
            />
          </Group>
          <Switch
            label="NFO 写入锁定标记"
            checked={movie.output.nfo_write_lockdata}
            onChange={(event) =>
              setOutput({ nfo_write_lockdata: event.currentTarget.checked })
            }
          />
          <Switch
            label="NFO 只写一个制片公司"
            checked={movie.output.nfo_single_studio}
            onChange={(event) =>
              setOutput({ nfo_single_studio: event.currentTarget.checked })
            }
          />
        </Stack>
        <SaveError error={save.error} />
        <Group>
          <Button
            loading={save.isPending}
            disabled={countryInvalid}
            onClick={() => {
              const skipFolders = skip
                .split("\n")
                .map((line) => line.trim())
                .filter((line) => line !== "");
              save.mutate(
                { ...movie, skip_folders: skipFolders },
                { onSuccess: () => setSkipText(null) },
              );
            }}
          >
            保存电影刮削设置
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

function FilesSection() {
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

function TasksSection() {
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
