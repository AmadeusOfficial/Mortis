import {
  Alert,
  Anchor,
  Button,
  Group,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { Link, useSearchParams } from "react-router";
import { type DataType, checkTmdb, fetchPlugins, fetchSecretsStatus, fetchSettings } from "../api";
import type { LoginResult } from "../components/PluginsSection";
import { PluginsSection, SourcePluginSettings } from "../components/PluginsSection";
import {
  AboutSection,
  FilesSection,
  NetworkSection,
  SecretField,
  TasksSection,
} from "../components/settings/GeneralSections";
import {
  BUILTIN_SOURCE_ENTRIES,
  BuiltinSourceSection,
  invalidateSourceStatus,
  isBuiltinSourceSection,
  useBuiltinSources,
} from "../components/settings/BuiltinSources";
import { PlaybackSection } from "../components/settings/PlaybackSection";
import { SettingCard } from "../components/settings/SettingLayout";
import { TypeSettings } from "../components/settings/TypeSettings";
import { PageHeader, Pill } from "../components/ui";
import { useDataTypes } from "../dataTypes";
import styles from "./SettingsPage.module.css";

export const DEFAULT_SECTION = "general.network";

type Entry = { key: string; label: string; available?: boolean };
type GroupDef = { group: string; entries: Entry[] };

const BUILTIN_ORDER = ["movie", "tv", "anime"];

// 内置类型按电影、电视剧、动漫排在前面，插件类型跟在后面按名字排。
function byBuiltinFirst(a: DataType, b: DataType): number {
  const rank = (type: DataType) => {
    const index = BUILTIN_ORDER.indexOf(type.id);
    return index === -1 ? BUILTIN_ORDER.length : index;
  };
  return rank(a) - rank(b) || a.name.localeCompare(b.name, "zh-CN");
}

export function SettingsPage() {
  const dataTypes = useDataTypes();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins });
  const secrets = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus });
  const settings = useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
  const builtinSources = useBuiltinSources();
  const [searchParams, setSearchParams] = useSearchParams();
  const narrow = useMediaQuery("(max-width: 62em)");

  // 后端 OAuth 登录后跳回 /settings?plugin=<id>&login=ok，失败时是 login=failed&reason=…。
  const loginStatus = searchParams.get("login");
  const loginPlugin = searchParams.get("plugin");
  const loginResult: LoginResult | null =
    loginPlugin !== null && (loginStatus === "ok" || loginStatus === "failed")
      ? { pluginId: loginPlugin, ok: loginStatus === "ok", reason: searchParams.get("reason") }
      : null;
  const clearLoginParam = useCallback(() => {
    setSearchParams(
      (params) => {
        params.delete("login");
        params.delete("reason");
        params.delete("plugin");
        return params;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  // 授权页跳回来时地址里只有 plugin 和 login，把它落到来源分区上，刷新也不丢。
  const hasSection = searchParams.get("section") !== null;
  useEffect(() => {
    if (loginPlugin === null || hasSection) {
      return;
    }
    setSearchParams(
      (params) => {
        params.set("section", `source.${loginPlugin}`);
        return params;
      },
      { replace: true },
    );
  }, [loginPlugin, hasSection, setSearchParams]);

  const players = (plugins.data ?? []).filter(
    (plugin) => plugin.kind === "player" && plugin.enabled && plugin.available === true,
  );
  const chosenPlayer = settings.data?.playback.player;
  const playbackReady =
    plugins.data === undefined || chosenPlayer === undefined
      ? undefined
      : chosenPlayer === ""
        ? players.length > 0
        : players.some((plugin) => plugin.id === chosenPlayer);

  const groups: GroupDef[] = [
    {
      group: "通用",
      entries: [
        { key: "general.network", label: "网络" },
        { key: "general.files", label: "文件" },
        { key: "general.tasks", label: "任务" },
        { key: "general.about", label: "关于" },
      ],
    },
    {
      group: "数据类型",
      entries: [...(dataTypes.data ?? [])].sort(byBuiltinFirst).map((type) => ({
        key: `type.${type.id}`,
        label: type.name,
      })),
    },
    {
      group: "来源",
      entries: [
        {
          key: "source.tmdb",
          label: "TMDB",
          available: secrets.data?.tmdb_api_key === true,
        },
        ...BUILTIN_SOURCE_ENTRIES.map((entry) => ({
          key: `source.${entry.id}`,
          label: entry.label,
          available: builtinSources.data?.find((source) => source.id === entry.id)?.available,
        })),
        ...(plugins.data ?? [])
          .filter((plugin) => plugin.kind === "source")
          .map((plugin) => ({
            key: `source.${plugin.id}`,
            label: plugin.name,
            available: plugin.enabled && plugin.error === null && plugin.notice === null,
          })),
      ],
    },
    {
      group: "播放",
      entries: [{ key: "playback", label: "播放器", available: playbackReady }],
    },
    { group: "插件", entries: [{ key: "plugins", label: "插件管理" }] },
  ];

  const keys = groups.flatMap((group) => group.entries.map((entry) => entry.key));
  const requested = searchParams.get("section");
  // 分区名可能是插件或类型列表还没读到时给的，形状对就先按它渲染，免得先闪一下默认页。
  const knownShape = (key: string): boolean =>
    keys.includes(key) ||
    key === "plugins" ||
    key === "playback" ||
    /^(general|type|source)\.[A-Za-z0-9_-]+$/.test(key);
  // 登录跳回来时先落到那个插件的来源页。
  const loginSection = loginPlugin === null ? null : `source.${loginPlugin}`;
  const current =
    requested !== null && knownShape(requested)
      ? requested
      : loginSection !== null && knownShape(loginSection)
        ? loginSection
        : keys.includes(DEFAULT_SECTION)
          ? DEFAULT_SECTION
          : (keys[0] ?? DEFAULT_SECTION);

  const select = (key: string) => {
    setSearchParams(
      (params) => {
        params.set("section", key);
        return params;
      },
      { replace: true },
    );
  };

  return (
    <div>
      <PageHeader
        title="设置"
        description="设置和密钥分两个文件保存在数据目录"
      />
      <div className={styles.layout}>
        {narrow ? (
          <Select
            w="100%"
            aria-label="设置分区"
            allowDeselect={false}
            value={current}
            onChange={(value) => value !== null && select(value)}
            data={groups.map((group) => ({
              group: group.group,
              items: group.entries.map((entry) => ({ value: entry.key, label: entry.label })),
            }))}
          />
        ) : (
          <nav className={styles.sideNav} aria-label="设置分区">
            {groups.map((group) => (
              <div key={group.group} className={styles.navGroup}>
                <div className={styles.navGroupTitle}>{group.group}</div>
                {group.entries.map((entry) => (
                  <Link
                    key={entry.key}
                    to={`/settings?section=${entry.key}`}
                    className={styles.navLink}
                    data-active={current === entry.key ? "true" : "false"}
                  >
                    <span className={styles.navLabel}>{entry.label}</span>
                    {entry.available !== undefined && (
                      <span
                        className={styles.navDot}
                        data-ok={entry.available ? "true" : "false"}
                        aria-hidden="true"
                      />
                    )}
                  </Link>
                ))}
              </div>
            ))}
          </nav>
        )}
        <div className={styles.content}>
          {/* 换分区时整块重挂载：上一个分区的草稿跟着丢掉，不会串到新分区。 */}
          <Section
            key={current}
            current={current}
            loginResult={loginResult}
            onLoginHandled={clearLoginParam}
          />
        </div>
      </div>
    </div>
  );
}

function Section({
  current,
  loginResult,
  onLoginHandled,
}: {
  current: string;
  loginResult: LoginResult | null;
  onLoginHandled: () => void;
}) {
  if (current === "general.network") {
    return <NetworkSection />;
  }
  if (current === "general.files") {
    return <FilesSection />;
  }
  if (current === "general.tasks") {
    return <TasksSection />;
  }
  if (current === "general.about") {
    return <AboutSection />;
  }
  if (current === "plugins") {
    return <PluginsSection />;
  }
  if (current === "playback") {
    return <PlaybackSection />;
  }
  if (current === "source.tmdb") {
    return <TmdbSection />;
  }
  if (current.startsWith("source.") && isBuiltinSourceSection(current.slice("source.".length))) {
    return <BuiltinSourceSection sourceId={current.slice("source.".length)} />;
  }
  if (current.startsWith("source.")) {
    return (
      <SourcePluginSettings
        pluginId={current.slice("source.".length)}
        loginResult={loginResult}
        onLoginHandled={onLoginHandled}
      />
    );
  }
  if (current.startsWith("type.")) {
    return <TypeSettings dataTypeId={current.slice("type.".length)} />;
  }
  return <Text c="var(--mo-dim)">没有这个设置分区。</Text>;
}

// TMDB 是内置类型的来源，密钥和插件密钥一起存在密钥文件里。
function TmdbSection() {
  const queryClient = useQueryClient();
  const check = useMutation({ mutationFn: checkTmdb });
  const status = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus });
  const filled = status.data?.tmdb_api_key ?? false;

  return (
    <SettingCard
      title="TMDB"
      description="用于：电影、电视剧、动漫"
      actions={
        <Pill tone={status.data === undefined ? "neutral" : filled ? "ok" : "warn"}>
          {status.data === undefined ? "正在读取…" : filled ? "Key 已填" : "Key 未填"}
        </Pill>
      }
    >
      <div className={styles.fieldList}>
        <SecretField
          secretKey="tmdb_api_key"
          label="TMDB API Key"
          hintExtra="元数据和图片来自 TMDB。可以填 API Key（v3）或读访问令牌（v4）。测试连接使用已保存的网络设置。"
          onChanged={() => {
            check.reset();
            invalidateSourceStatus(queryClient);
          }}
        />
      </div>
      <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
        <Stack gap="sm">
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Button variant="default" loading={check.isPending} onClick={() => check.mutate()}>
              测试连接
            </Button>
            <Anchor component={Link} to="/settings?section=type.movie" size="sm" fw={600}>
              打开电影设置
            </Anchor>
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
      </div>
    </SettingCard>
  );
}
