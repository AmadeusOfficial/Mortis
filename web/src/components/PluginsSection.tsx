import {
  Alert,
  Anchor,
  Avatar,
  Button,
  CopyButton,
  Group,
  Modal,
  NumberInput,
  PasswordInput,
  Radio,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import {
  type DataType,
  type JsonSchema,
  type JsonSchemaProperty,
  type PackagePreview,
  type PluginCapability,
  type PluginInfo,
  checkPluginPackage,
  fetchDataTypes,
  fetchPluginAccount,
  fetchPlugins,
  logoutPlugin,
  pluginLoginUrl,
  providesAlone,
  reloadPlugins,
  updatePlugin,
} from "../api";
import styles from "../pages/SettingsPage.module.css";
import { IconRefresh } from "../icons";
import { PathPicker } from "./PathPicker";
import { PluginImportModal, UninstallModal } from "./PluginImportModal";
import { SettingCard, SettingRow } from "./settings/SettingLayout";
import { Pill } from "./ui";

const CAPABILITY_LABELS: Record<PluginCapability, string> = {
  data_type: "数据类型",
  source: "来源",
  player: "播放",
  hook: "事件钩子",
};

// 去哪里改这一条的设置：来源在「来源」，播放在「播放」或它所属数据类型的页面。
// 带成员的包只是容器，设置在它的成员上。
function settingsPath(plugin: PluginInfo, parentPlugin: PluginInfo | undefined): string | null {
  if (providesAlone(plugin, "source")) {
    return `/settings?section=source.${plugin.id}`;
  }
  if (providesAlone(plugin, "player")) {
    return parentPlugin?.capabilities.includes("data_type")
      ? `/settings?section=type.${parentPlugin.data_type ?? parentPlugin.id}`
      : "/settings?section=playback";
  }
  return null;
}

const ORIGIN_LABELS: Record<PluginInfo["origin"], string> = {
  builtin: "自带",
  user: "数据目录",
};

// 后端从 Unix 秒返回到期时间，这里按本地时间写成年月日时分。
function formatExpiry(seconds: number): string {
  const date = new Date(seconds * 1000);
  const pad = (part: number) => String(part).padStart(2, "0");
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function schemaEntries(schema: JsonSchema | null): [string, JsonSchemaProperty][] {
  return Object.entries(schema?.properties ?? {});
}

function asText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return value === undefined || value === null ? "" : String(value);
}

function asNumber(value: unknown): number | "" {
  return typeof value === "number" ? value : "";
}

function initialValue(property: JsonSchemaProperty, saved: unknown): unknown {
  if (saved !== undefined && saved !== null) {
    return saved;
  }
  if (property.default !== undefined) {
    return property.default;
  }
  if (property.type === "boolean") {
    return false;
  }
  return property.type === "integer" || property.type === "number" ? 0 : "";
}

export interface LoginResult {
  pluginId: string;
  ok: boolean;
  reason: string | null;
}

// 把插件列表读出来，并给出「服务哪种数据类型」的名字。
export function usePlugins() {
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins });
  const dataTypes = useQuery({ queryKey: ["data-types"], queryFn: fetchDataTypes });
  const typeName = (id: string | null): string => {
    if (id === null) {
      return "";
    }
    const found: DataType | undefined = dataTypes.data?.find((type) => type.id === id);
    return found?.name ?? id;
  };
  return { plugins, dataTypes, typeName };
}

// 启用/停用一个插件：成功后刷新插件、数据类型和相关列表。
function useToggleMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updatePlugin(id, { enabled }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["plugins"] });
      void queryClient.invalidateQueries({ queryKey: ["data-types"] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      void queryClient.invalidateQueries({ queryKey: ["source-orders"] });
      notifications.show({
        message: saved.enabled ? `已启用 ${saved.name}` : `已停用 ${saved.name}`,
      });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });
}

// 插件页：只管理启用、重载和加载错误，设置表单在各来源和类型页里。
export function PluginsSection() {
  const { plugins, typeName } = usePlugins();
  const queryClient = useQueryClient();

  const reload = useMutation({
    mutationFn: reloadPlugins,
    onSuccess: (list) => {
      queryClient.setQueryData(["plugins"], list);
      void queryClient.invalidateQueries({ queryKey: ["data-types"] });
      void queryClient.invalidateQueries({ queryKey: ["libraries"] });
      void queryClient.invalidateQueries({ queryKey: ["source-orders"] });
      notifications.show({ color: "green", message: `已重新加载 ${list.length} 个插件` });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  const toggle = useToggleMutation();
  const fileInput = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<PackagePreview | null>(null);
  const [removing, setRemoving] = useState<PluginInfo | null>(null);
  // 上传只做静态检查，不执行插件代码；通过后弹出预览，由用户确认。
  const check = useMutation({
    mutationFn: checkPluginPackage,
    onSuccess: setPreview,
    onError: (error) =>
      notifications.show({ color: "red", title: "这个压缩包不能导入", message: error.message }),
  });

  return (
    <SettingCard
      title="插件"
      description="插件从自带的 plugins 目录和数据目录的 plugins 目录加载。停用后不参与识别和刮削，已有的条目保留。来源插件的设置在「来源」里，播放插件的设置在「播放」里。"
      actions={
        <Group gap="xs">
          <input
            ref={fileInput}
            type="file"
            accept=".zip,application/zip"
            hidden
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file !== undefined) {
                check.mutate(file);
              }
            }}
          />
          <Button
            variant="default"
            size="sm"
            loading={check.isPending}
            onClick={() => fileInput.current?.click()}
          >
            导入插件（zip）
          </Button>
          <Button
            variant="default"
            size="sm"
            leftSection={<IconRefresh size={15} />}
            loading={reload.isPending}
            onClick={() => reload.mutate()}
          >
            重新加载插件
          </Button>
        </Group>
      }
    >
      {plugins.isError && (
        <div className={styles.cardBody}>
          <Alert color="red" title="读不到插件">
            {plugins.error.message}
          </Alert>
        </div>
      )}
      {plugins.isPending && (
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            正在读取插件…
          </Text>
        </div>
      )}
      {plugins.isSuccess && plugins.data.length === 0 && (
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            还没有插件。
          </Text>
        </div>
      )}
      {plugins.data
        ?.filter((plugin) => plugin.parent === null)
        .map((plugin) => {
          const children = (plugins.data ?? []).filter((item) => item.parent === plugin.id);
          return (
            <div key={plugin.id} className={styles.listRowCol}>
              <PluginRow
                plugin={plugin}
                typeName={typeName}
                toggle={toggle}
                onUninstall={setRemoving}
              />
              {children.length > 0 && (
                <div className={styles.childSources}>
                  {children.map((child) => (
                    <PluginRow
                      key={child.id}
                      plugin={child}
                      typeName={typeName}
                      toggle={toggle}
                      parentPlugin={plugin}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      {preview !== null && <PluginImportModal preview={preview} onClose={() => setPreview(null)} />}
      {removing !== null && (
        <UninstallModal id={removing.id} name={removing.name} onClose={() => setRemoving(null)} />
      )}
    </SettingCard>
  );
}

// 插件页里的一行：顶层插件或缩进在数据类型下的子来源。
function PluginRow({
  plugin,
  typeName,
  toggle,
  parentPlugin,
  onUninstall,
}: {
  plugin: PluginInfo;
  typeName: (id: string | null) => string;
  toggle: ReturnType<typeof useToggleMutation>;
  parentPlugin?: PluginInfo;
  onUninstall?: (plugin: PluginInfo) => void;
}) {
  const permissions = [...new Set(plugin.hooks.flatMap((hook) => hook.writes))];
  // 父插件停用时，子来源整行变灰、开关禁用，状态随父恢复。
  const parentDisabled = parentPlugin !== undefined && !parentPlugin.enabled;
  // 父插件正在切换时也锁住子来源开关，避免并发竞态。
  const parentToggling =
    parentPlugin !== undefined && toggle.isPending && toggle.variables?.id === parentPlugin.id;
  // 父插件自身有加载错误时，子来源的 error 一律不显示：后端会给子错误带「父插件…加载失败」
  // 前缀，字符串比较去不了重，所以只要父有 error 就当作同一个问题，不在子行重复报。
  const showError =
    plugin.error !== null && (parentPlugin === undefined || parentPlugin.error === null);
  const settingsTarget = settingsPath(plugin, parentPlugin);
  return (
    <>
      <Group
        justify="space-between"
        align="flex-start"
        wrap="nowrap"
        gap="md"
        style={parentDisabled ? { opacity: 0.55 } : undefined}
      >
        <Stack gap={4} style={{ minWidth: 0 }}>
          <Group gap="xs" wrap="wrap">
            <Text size="sm" fw={600}>
              {plugin.name}
            </Text>
            {plugin.version !== "" && (
              <Pill tone="neutral">
                <span className="mo-mono">{plugin.version}</span>
              </Pill>
            )}
            {plugin.capabilities.map((capability) => (
              <Pill key={capability} tone={capability === "source" ? "accent" : "neutral"}>
                {CAPABILITY_LABELS[capability]}
              </Pill>
            ))}
            <Pill tone="neutral">{ORIGIN_LABELS[plugin.origin]}</Pill>
          </Group>
          {/* 顶层来源才标数据类型；缩进在分组里的子来源已经一目了然，不重复。 */}
          {providesAlone(plugin, "source") && parentPlugin === undefined && (
            <Text size="xs" c="var(--mo-muted)">
              服务的数据类型：{typeName(plugin.data_type)}
            </Text>
          )}
          {plugin.hooks.length > 0 && (
            <Text size="xs" c="var(--mo-muted)">
              订阅事件：{plugin.hooks.map((hook) => hook.event).join("、")}
            </Text>
          )}
          {permissions.length > 0 && (
            <Text size="xs" c="var(--mo-muted)">
              权限声明（可写）：{permissions.join("、")}
            </Text>
          )}
          {parentDisabled && (
            <Text size="xs" c="var(--mo-muted)">
              随 {parentPlugin?.name} 停用
            </Text>
          )}
        </Stack>
        <Group gap="md" wrap="nowrap" style={{ flexShrink: 0 }}>
          {settingsTarget !== null && (
            <Anchor component={Link} to={settingsTarget} size="sm" fw={600}>
              去设置
            </Anchor>
          )}
          <Switch
            label="启用"
            checked={plugin.enabled}
            disabled={
              parentDisabled ||
              parentToggling ||
              (toggle.isPending && toggle.variables?.id === plugin.id)
            }
            onChange={(event) => toggle.mutate({ id: plugin.id, enabled: event.currentTarget.checked })}
          />
          {/* 内置插件和包带的子条目不显示卸载。 */}
          {plugin.origin === "user" && parentPlugin === undefined && onUninstall !== undefined && (
            <Button variant="subtle" color="red" size="xs" onClick={() => onUninstall(plugin)}>
              卸载
            </Button>
          )}
        </Group>
      </Group>
      {plugin.restart_pending && (
        <Alert color="yellow" title="需要重启">
          这个插件被覆盖升级过，磁盘上已是新版，要重启莫提斯才完全生效。
        </Alert>
      )}
      {showError && (
        <Alert color="red" title="加载失败">
          {plugin.error}
        </Alert>
      )}
      {plugin.notice !== null && <Alert color="yellow">{plugin.notice}</Alert>}
    </>
  );
}

// 一个来源插件的设置页：设置表单，需要登录的插件再加账号状态和登录方式。
export function SourcePluginSettings({
  pluginId,
  loginResult,
  onLoginHandled,
}: {
  pluginId: string;
  loginResult: LoginResult | null;
  onLoginHandled: () => void;
}) {
  const { plugins, typeName } = usePlugins();
  const queryClient = useQueryClient();
  const loginHandled = useRef(false);
  const plugin = plugins.data?.find((item) => item.id === pluginId) ?? null;

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    void queryClient.invalidateQueries({ queryKey: ["sources"] });
  }, [queryClient]);

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => updatePlugin(id, { enabled }),
    onSuccess: (saved) => {
      refresh();
      notifications.show({
        message: saved.enabled ? `已启用 ${saved.name}` : `已停用 ${saved.name}`,
      });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  useEffect(() => {
    if (loginResult === null || loginHandled.current || plugin === null) {
      return;
    }
    if (loginResult.pluginId !== plugin.id) {
      return;
    }
    loginHandled.current = true;
    notifications.show(
      loginResult.ok
        ? { color: "green", message: `${plugin.name} 登录成功` }
        : { color: "red", title: `${plugin.name} 没有登录`, message: loginResult.reason ?? "授权没有完成" },
    );
    void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    void queryClient.invalidateQueries({ queryKey: ["plugin-account", loginResult.pluginId] });
    onLoginHandled();
  }, [loginResult, plugin, queryClient, onLoginHandled]);

  if (plugins.isError) {
    return (
      <Alert color="red" title="读不到插件">
        {plugins.error.message}
      </Alert>
    );
  }
  if (plugins.isPending) {
    return <Text c="var(--mo-dim)">正在读取插件…</Text>;
  }
  if (plugin === null) {
    return (
      <Alert color="yellow" title="没有这个来源插件">
        {pluginId} 没有安装或没有加载。
      </Alert>
    );
  }

  return (
    <Stack gap="md">
      <SettingCard
        title={
          <Group gap="xs" wrap="wrap">
            <span>{plugin.name}</span>
            {plugin.version !== "" && (
              <Pill tone="neutral">
                <span className="mo-mono">{plugin.version}</span>
              </Pill>
            )}
            <Pill tone="accent">来源</Pill>
          </Group>
        }
        description={`用于：${typeName(plugin.data_type) || "（未知类型）"}`}
        actions={
          <Switch
            label="启用"
            checked={plugin.enabled}
            disabled={toggle.isPending}
            onChange={(event) => toggle.mutate({ id: plugin.id, enabled: event.currentTarget.checked })}
          />
        }
      >
        {(plugin.error !== null || plugin.notice !== null) && (
          <div className={styles.cardBody}>
            <Stack gap="sm">
              {plugin.error !== null && (
                <Alert color="red" title="加载失败">
                  {plugin.error}
                </Alert>
              )}
              {plugin.notice !== null && <Alert color="yellow">{plugin.notice}</Alert>}
            </Stack>
          </div>
        )}
      </SettingCard>

      <PluginSettingsForm plugin={plugin} onChanged={refresh} />

      {plugin.oauth ? (
        <PluginLogin plugin={plugin} onChanged={refresh} />
      ) : (
        <PluginSecretsForm plugin={plugin} onChanged={refresh} />
      )}
    </Stack>
  );
}

// 设置表单只认 JSON Schema 的一个子集（和 plugin_api 模块文档一致）：
// boolean 是开关，integer 和 number 是数字框，string 是文本框，
// format 为 "file-path" 的 string 是带文件选择的路径框；title 当标签，description 当提示，
// default 当初始值。嵌套对象、数组、enum 都按文本框处理。密钥只有字符串，一律是密码框。
function PluginSchemaField({
  fieldKey,
  property,
  value,
  onChange,
}: {
  fieldKey: string;
  property: JsonSchemaProperty;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const inputId = useId();
  const hintId = useId();
  const label = property.title ?? fieldKey;

  if (property.type === "boolean") {
    return (
      <SettingRow label={label} hint={property.description} htmlFor={inputId} hintId={hintId}>
        <Switch
          id={inputId}
          aria-describedby={property.description ? hintId : undefined}
          checked={value === true}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
      </SettingRow>
    );
  }
  if (property.type === "integer" || property.type === "number") {
    return (
      <SettingRow label={label} hint={property.description} htmlFor={inputId} hintId={hintId}>
        <NumberInput
          id={inputId}
          aria-describedby={property.description ? hintId : undefined}
          className={styles.monoInput}
          maw={200}
          allowDecimal={property.type === "number"}
          value={asNumber(value)}
          onChange={(next) => onChange(typeof next === "number" ? next : 0)}
        />
      </SettingRow>
    );
  }
  if (property.format === "file-path") {
    return (
      <SettingRow label={label} hint={property.description} htmlFor={inputId} hintId={hintId}>
        <Group gap="xs" wrap="nowrap">
          <PathPicker
            id={inputId}
            describedBy={property.description ? hintId : undefined}
            label={label}
            mode="file"
            value={asText(value)}
            onChange={onChange}
          />
        </Group>
      </SettingRow>
    );
  }
  return (
    <SettingRow label={label} hint={property.description} htmlFor={inputId} hintId={hintId}>
      <TextInput
        id={inputId}
        aria-describedby={property.description ? hintId : undefined}
        value={asText(value)}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </SettingRow>
  );
}

export function PluginSettingsForm({
  plugin,
  onChanged,
  title = "设置",
  saveLabel = "保存设置",
}: {
  plugin: PluginInfo;
  onChanged: () => void;
  title?: string;
  saveLabel?: string;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(
      schemaEntries(plugin.settings_schema).map(([key, property]) => [
        key,
        initialValue(property, plugin.settings?.[key]),
      ]),
    ),
  );
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => updatePlugin(plugin.id, { settings: values }),
    onSuccess: (updated) => {
      onChanged();
      notifications.show({ color: "green", message: `${updated.name} 的设置已保存` });
    },
  });
  const entries = schemaEntries(plugin.settings_schema);
  if (entries.length === 0) {
    return (
      <SettingCard title={title}>
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            {plugin.name} 没有可改的设置。
          </Text>
        </div>
      </SettingCard>
    );
  }
  const set = (key: string, value: unknown) => setDraft({ ...draft, [key]: value });

  return (
    <SettingCard title={title} description={`配置 ${plugin.name} 的运行参数`}>
      <div className={styles.fieldList}>
        {entries.map(([key, property]) => (
          <PluginSchemaField
            key={key}
            fieldKey={key}
            property={property}
            value={draft[key]}
            onChange={(next) => set(key, next)}
          />
        ))}
      </div>
      <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
        <Stack gap="sm">
          {save.isError && (
            <Alert color="red" title="保存失败">
              {save.error.message}
            </Alert>
          )}
          <Group>
            <Button variant="filled" loading={save.isPending} onClick={() => save.mutate(draft)}>
              {saveLabel}
            </Button>
          </Group>
        </Stack>
      </div>
    </SettingCard>
  );
}

function PluginSecretRow({
  fieldKey,
  property,
  isSet,
  value,
  busy,
  onChange,
  onClear,
}: {
  fieldKey: string;
  property: JsonSchemaProperty;
  isSet: boolean;
  value: string;
  busy: boolean;
  onChange: (next: string) => void;
  onClear: () => void;
}) {
  const inputId = useId();
  const hintId = useId();
  const label = property.title ?? fieldKey;
  const hint = [
    isSet ? "已填写。输入新值会覆盖，留空不修改。" : "未填写。",
    property.description,
  ]
    .filter(Boolean)
    .join("");

  return (
    <SettingRow label={label} hint={hint} htmlFor={inputId} hintId={hintId} align="start">
      <Group align="center" gap="xs" wrap="nowrap">
        <PasswordInput
          id={inputId}
          aria-describedby={hintId}
          style={{ flex: 1 }}
          autoComplete="new-password"
          value={value}
          onChange={(event) => onChange(event.currentTarget.value)}
        />
        <Button
          variant="subtle"
          color="red"
          aria-label={`清除${label}`}
          disabled={!isSet || busy}
          onClick={onClear}
        >
          清除
        </Button>
      </Group>
    </SettingRow>
  );
}

function PluginSecretsForm({ plugin, onChanged }: { plugin: PluginInfo; onChanged: () => void }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: (values: Record<string, string>) => updatePlugin(plugin.id, { secrets: values }),
    onSuccess: (saved, values) => {
      setDraft({});
      onChanged();
      const cleared = Object.values(values).every((value) => value.trim() === "");
      notifications.show({
        color: "green",
        message: cleared ? `${saved.name} 的密钥已清除` : `${saved.name} 的密钥已保存`,
      });
    },
  });
  const entries = schemaEntries(plugin.secrets_schema);
  if (entries.length === 0) {
    return (
      <SettingCard title="密钥">
        <div className={styles.cardBody}>
          <Text size="sm" c="var(--mo-dim)">
            {plugin.name} 没有需要填的密钥。
          </Text>
        </div>
      </SettingCard>
    );
  }
  // 只提交填了内容的键；清空由每行的「清除」按钮负责。
  const filled = entries
    .map(([key]) => [key, (draft[key] ?? "").trim()] as const)
    .filter(([, value]) => value !== "");

  return (
    <SettingCard title="密钥" description="密钥单独保存在数据目录的密钥文件中">
      <div className={styles.fieldList}>
        {entries.map(([key, property]) => (
          <PluginSecretRow
            key={key}
            fieldKey={key}
            property={property}
            isSet={plugin.secrets?.[key] === true}
            value={draft[key] ?? ""}
            busy={save.isPending}
            onChange={(next) => setDraft({ ...draft, [key]: next })}
            onClear={() => save.mutate({ [key]: "" })}
          />
        ))}
      </div>
      <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
        <Stack gap="sm">
          {save.isError && (
            <Alert color="red" title="保存失败">
              {save.error.message}
            </Alert>
          )}
          <Group>
            <Button
              variant="filled"
              loading={save.isPending}
              disabled={filled.length === 0}
              onClick={() => save.mutate(Object.fromEntries(filled))}
            >
              保存密钥
            </Button>
          </Group>
        </Stack>
      </div>
    </SettingCard>
  );
}

type LoginMode = "oauth" | "token";

// 需要登录的来源：顶部是账号状态，登录时选 OAuth 快捷登录或手填访问令牌。
function PluginLogin({ plugin, onChanged }: { plugin: PluginInfo; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const [choosing, setChoosing] = useState(false);
  const account = useQuery({
    queryKey: ["plugin-account", plugin.id],
    queryFn: () => fetchPluginAccount(plugin.id),
    enabled: plugin.logged_in,
    retry: false,
  });
  const logout = useMutation({
    mutationFn: () => logoutPlugin(plugin.id),
    onSuccess: (saved) => {
      queryClient.removeQueries({ queryKey: ["plugin-account", plugin.id] });
      onChanged();
      notifications.show({ color: "green", message: `已退出 ${saved.name}` });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });
  const who = account.data?.account ?? null;
  const detail = !plugin.logged_in
    ? `还没登录 ${plugin.name} 账号`
    : plugin.expires_at !== null
      ? `令牌到期 ${formatExpiry(plugin.expires_at)}，到期前自动续期`
      : "使用手填的访问令牌";

  return (
    <SettingCard title="账号状态" description="授权登录或管理访问令牌">
      <div className={styles.cardBody}>
        <div className={styles.accountCard}>
          <Group justify="space-between" wrap="nowrap" gap="md">
            <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
              <Avatar src={who?.avatar_url ?? null} radius="xl" size="lg" alt={who?.name ?? ""} />
              <Stack gap={2} style={{ minWidth: 0 }}>
                {who !== null ? (
                  who.url !== null ? (
                    <Anchor href={who.url} target="_blank" rel="noreferrer" size="sm" fw={600}>
                      {who.name}
                    </Anchor>
                  ) : (
                    <Text size="sm" fw={600}>
                      {who.name}
                    </Text>
                  )
                ) : (
                  <Text size="sm" fw={600}>
                    {plugin.logged_in ? plugin.name : "未登录"}
                  </Text>
                )}
                {plugin.logged_in && account.isFetching && who === null && (
                  <Text size="sm" c="var(--mo-dim)">
                    正在读取账号…
                  </Text>
                )}
                {account.data?.error != null && (
                  <Text size="sm" c="var(--mo-bad)">
                    {account.data.error}
                  </Text>
                )}
                <Text size="xs" c="var(--mo-muted)">
                  {detail}
                </Text>
              </Stack>
            </Group>
            <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
              <Button variant={plugin.logged_in ? "default" : "filled"} onClick={() => setChoosing(true)}>
                {plugin.logged_in ? "重新登录" : "登录"}
              </Button>
              {plugin.logged_in && (
                <Button variant="subtle" color="red" loading={logout.isPending} onClick={() => logout.mutate()}>
                  退出登录
                </Button>
              )}
            </Group>
          </Group>
        </div>
        {choosing && (
          <LoginModal plugin={plugin} onChanged={onChanged} onClose={() => setChoosing(false)} />
        )}
      </div>
    </SettingCard>
  );
}

function LoginModal({
  plugin,
  onChanged,
  onClose,
}: {
  plugin: PluginInfo;
  onChanged: () => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [idKey, secretKey] = plugin.oauth_client_keys;
  const hasApp = plugin.secrets?.[idKey] === true && plugin.secrets?.[secretKey] === true;
  const canPaste = plugin.oauth_token_page_url !== "";
  const [mode, setMode] = useState<LoginMode>("oauth");
  const [editingApp, setEditingApp] = useState(!hasApp);
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [token, setToken] = useState("");
  const callback = `${window.location.origin}/api/plugins/${plugin.id}/oauth/callback`;

  const oauth = useMutation({
    mutationFn: async () => {
      if (editingApp) {
        await updatePlugin(plugin.id, {
          secrets: { [idKey]: appId.trim(), [secretKey]: appSecret.trim() },
        });
      }
    },
    // 授权要整页跳到来源的授权页，登录完由后端跳回设置页。
    onSuccess: () => window.location.assign(pluginLoginUrl(plugin.id)),
  });
  const paste = useMutation({
    mutationFn: async () => {
      await updatePlugin(plugin.id, { secrets: { access_token: token.trim() } });
      const result = await fetchPluginAccount(plugin.id);
      if (result.error !== null) {
        await logoutPlugin(plugin.id);
        throw new Error(result.error);
      }
      return result;
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["plugin-account", plugin.id], result);
      onChanged();
      onClose();
      notifications.show({
        color: "green",
        message: `${plugin.name} 登录成功${result.account !== null ? `：${result.account.name}` : ""}`,
      });
    },
    onError: () => onChanged(),
  });
  const appReady = !editingApp || (appId.trim() !== "" && appSecret.trim() !== "");
  const busy = oauth.isPending || paste.isPending;

  return (
    <Modal opened onClose={onClose} title="选择登录方式">
      <Stack>
        <Radio.Group value={mode} onChange={(value) => setMode(value as LoginMode)}>
          <Stack gap="xs">
            <Radio value="oauth" label="OAuth 快捷登录" />
            {canPaste && <Radio value="token" label="AccessToken 登录" />}
          </Stack>
        </Radio.Group>
        {mode === "oauth" && !editingApp && (
          <Stack gap={4}>
            <Text size="sm">将跳到 {plugin.name} 的授权页，登录并同意授权后自动回到这里。</Text>
            <Group gap={4}>
              <Text size="xs" c="var(--mo-muted)">
                已保存 OAuth 应用。
              </Text>
              <Anchor component="button" type="button" size="xs" onClick={() => setEditingApp(true)}>
                更换应用
              </Anchor>
            </Group>
          </Stack>
        )}
        {mode === "oauth" && editingApp && (
          <Stack gap="xs">
            <Text size="sm">
              第一次使用要建一个自己的 OAuth 应用（只需一次）：
              {plugin.oauth_register_url !== "" ? (
                <Anchor href={plugin.oauth_register_url} target="_blank" rel="noreferrer">
                  {" "}
                  打开应用管理页
                </Anchor>
              ) : null}
              ，新建应用，回调地址填下面这个，然后把 App ID 和 App Secret 填进来。
            </Text>
            <Group gap="xs" wrap="nowrap">
              <TextInput value={callback} readOnly style={{ flex: 1 }} aria-label="回调地址" />
              <CopyButton value={callback}>
                {({ copied, copy }) => (
                  <Button variant="default" onClick={copy}>
                    {copied ? "已复制" : "复制"}
                  </Button>
                )}
              </CopyButton>
            </Group>
            <TextInput
              label="App ID"
              value={appId}
              onChange={(event) => setAppId(event.currentTarget.value)}
            />
            <PasswordInput
              label="App Secret"
              autoComplete="new-password"
              value={appSecret}
              onChange={(event) => setAppSecret(event.currentTarget.value)}
            />
            {hasApp && (
              <Anchor component="button" type="button" size="xs" onClick={() => setEditingApp(false)}>
                不换了，用已保存的应用
              </Anchor>
            )}
          </Stack>
        )}
        {mode === "token" && (
          <Stack gap="xs">
            <Text size="sm">
              在
              <Anchor href={plugin.oauth_token_page_url} target="_blank" rel="noreferrer">
                {" "}
                令牌页{" "}
              </Anchor>
              生成一个访问令牌，粘贴到这里。手填的令牌不会自动续期，过期后要重新填。
            </Text>
            <PasswordInput
              label="访问令牌"
              autoComplete="new-password"
              value={token}
              onChange={(event) => setToken(event.currentTarget.value)}
            />
          </Stack>
        )}
        {(oauth.isError || paste.isError) && (
          <Alert color="red" title="没有登录">
            {(oauth.error ?? paste.error)?.message}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="filled"
            loading={busy}
            disabled={mode === "oauth" ? !appReady : token.trim() === ""}
            onClick={() => (mode === "oauth" ? oauth.mutate() : paste.mutate())}
          >
            登录
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
