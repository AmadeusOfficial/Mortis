import {
  Alert,
  Anchor,
  Avatar,
  Badge,
  Button,
  CopyButton,
  Group,
  Modal,
  NumberInput,
  Paper,
  PasswordInput,
  Radio,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  type DataType,
  type JsonSchema,
  type JsonSchemaProperty,
  type PluginInfo,
  fetchDataTypes,
  fetchPluginAccount,
  fetchPlugins,
  logoutPlugin,
  pluginLoginUrl,
  reloadPlugins,
  updatePlugin,
} from "../api";

const KIND_LABELS: Record<PluginInfo["kind"], string> = {
  data_type: "数据类型",
  source: "来源",
};

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

  const toggle = useMutation({
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

  return (
    <Paper withBorder p="md">
      <Stack>
        <Group justify="space-between">
          <Title order={4}>插件</Title>
          <Button variant="default" loading={reload.isPending} onClick={() => reload.mutate()}>
            重新加载插件
          </Button>
        </Group>
        <Text size="sm" c="dimmed">
          插件从自带的 plugins 目录和数据目录的 plugins 目录加载。停用后不参与识别和刮削，已有的条目保留。来源插件的设置在「来源」里。
        </Text>
        {plugins.isError && (
          <Alert color="red" title="读不到插件">
            {plugins.error.message}
          </Alert>
        )}
        {plugins.isPending && <Text c="dimmed">正在读取插件…</Text>}
        {plugins.isSuccess && plugins.data.length === 0 && <Text c="dimmed">还没有插件。</Text>}
        {plugins.data?.map((plugin) => (
          <Paper key={plugin.id} withBorder p="sm">
            <Stack gap="sm">
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <Stack gap={4}>
                  <Group gap="xs">
                    <Text fw={500}>{plugin.name}</Text>
                    {plugin.version !== "" && (
                      <Badge variant="outline" color="gray">
                        {plugin.version}
                      </Badge>
                    )}
                    <Badge variant="light">{KIND_LABELS[plugin.kind]}</Badge>
                    <Badge variant="outline" color="gray">
                      {ORIGIN_LABELS[plugin.origin]}
                    </Badge>
                  </Group>
                  {plugin.kind === "source" && (
                    <Text size="sm" c="dimmed">
                      服务的数据类型：{typeName(plugin.data_type)}
                    </Text>
                  )}
                  {plugin.kind === "source" && (
                    <Anchor component={Link} to={`/settings?section=source.${plugin.id}`} size="sm">
                      去设置
                    </Anchor>
                  )}
                </Stack>
                <Switch
                  label="启用"
                  checked={plugin.enabled}
                  disabled={toggle.isPending && toggle.variables?.id === plugin.id}
                  onChange={(event) => toggle.mutate({ id: plugin.id, enabled: event.currentTarget.checked })}
                />
              </Group>
              {plugin.error !== null && (
                <Alert color="red" title="加载失败">
                  {plugin.error}
                </Alert>
              )}
              {plugin.notice !== null && <Alert color="yellow">{plugin.notice}</Alert>}
            </Stack>
          </Paper>
        ))}
      </Stack>
    </Paper>
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
    return <Text c="dimmed">正在读取插件…</Text>;
  }
  if (plugin === null) {
    return (
      <Alert color="yellow" title="没有这个来源插件">
        {pluginId} 没有安装或没有加载。
      </Alert>
    );
  }

  return (
    <Stack>
      <Paper withBorder p="md">
        <Stack gap="sm">
          <Group justify="space-between" wrap="nowrap">
            <Stack gap={4}>
              <Group gap="xs">
                <Title order={4}>{plugin.name}</Title>
                {plugin.version !== "" && (
                  <Badge variant="outline" color="gray">
                    {plugin.version}
                  </Badge>
                )}
                <Badge variant="light">来源</Badge>
              </Group>
              <Text size="sm" c="dimmed">
                用于：{typeName(plugin.data_type) || "（未知类型）"}
              </Text>
            </Stack>
            <Switch
              label="启用"
              checked={plugin.enabled}
              disabled={toggle.isPending}
              onChange={(event) => toggle.mutate({ id: plugin.id, enabled: event.currentTarget.checked })}
            />
          </Group>
          {plugin.error !== null && (
            <Alert color="red" title="加载失败">
              {plugin.error}
            </Alert>
          )}
          {plugin.notice !== null && <Alert color="yellow">{plugin.notice}</Alert>}
        </Stack>
      </Paper>
      <Paper withBorder p="md">
        <PluginSettingsForm plugin={plugin} onChanged={refresh} />
      </Paper>
      <Paper withBorder p="md">
        {plugin.oauth ? (
          <PluginLogin plugin={plugin} onChanged={refresh} />
        ) : (
          <PluginSecretsForm plugin={plugin} onChanged={refresh} />
        )}
      </Paper>
    </Stack>
  );
}

function PluginSettingsForm({ plugin, onChanged }: { plugin: PluginInfo; onChanged: () => void }) {
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
      <Text size="sm" c="dimmed">
        {plugin.name} 没有可改的设置。
      </Text>
    );
  }
  const set = (key: string, value: unknown) => setDraft({ ...draft, [key]: value });

  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        设置
      </Text>
      {entries.map(([key, property]) => {
        const label = property.title ?? key;
        if (property.type === "boolean") {
          return (
            <Switch
              key={key}
              label={label}
              description={property.description}
              checked={draft[key] === true}
              onChange={(event) => set(key, event.currentTarget.checked)}
            />
          );
        }
        if (property.type === "integer" || property.type === "number") {
          return (
            <NumberInput
              key={key}
              label={label}
              description={property.description}
              allowDecimal={property.type === "number"}
              value={asNumber(draft[key])}
              onChange={(value) => set(key, typeof value === "number" ? value : 0)}
            />
          );
        }
        return (
          <TextInput
            key={key}
            label={label}
            description={property.description}
            value={asText(draft[key])}
            onChange={(event) => set(key, event.currentTarget.value)}
          />
        );
      })}
      {save.isError && (
        <Text size="sm" c="red">
          {save.error.message}
        </Text>
      )}
      <Group>
        <Button variant="light" loading={save.isPending} onClick={() => save.mutate(draft)}>
          保存设置
        </Button>
      </Group>
    </Stack>
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
      <Text size="sm" c="dimmed">
        {plugin.name} 没有需要填的密钥。
      </Text>
    );
  }
  // 只提交填了内容的键；清空由每行的「清除」按钮负责。
  const filled = entries
    .map(([key]) => [key, (draft[key] ?? "").trim()] as const)
    .filter(([, value]) => value !== "");

  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        密钥
      </Text>
      {entries.map(([key, property]) => (
        <Group key={key} align="flex-end" wrap="nowrap">
          <PasswordInput
            style={{ flex: 1 }}
            label={property.title ?? key}
            description={[
              plugin.secrets?.[key] === true ? "已填写。输入新值会覆盖，留空不修改。" : "未填写。",
              property.description,
            ]
              .filter(Boolean)
              .join("")}
            autoComplete="new-password"
            value={draft[key] ?? ""}
            onChange={(event) => setDraft({ ...draft, [key]: event.currentTarget.value })}
          />
          <Button
            variant="subtle"
            color="red"
            aria-label={`清除${property.title ?? key}`}
            disabled={plugin.secrets?.[key] !== true || save.isPending}
            onClick={() => save.mutate({ [key]: "" })}
          >
            清除
          </Button>
        </Group>
      ))}
      {save.isError && (
        <Text size="sm" c="red">
          {save.error.message}
        </Text>
      )}
      <Group>
        <Button
          variant="light"
          loading={save.isPending}
          disabled={filled.length === 0}
          onClick={() => save.mutate(Object.fromEntries(filled))}
        >
          保存密钥
        </Button>
      </Group>
    </Stack>
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
    <Paper withBorder p="sm" bg="gray.0">
      <Group justify="space-between" wrap="nowrap">
        <Group gap="sm" wrap="nowrap">
          <Avatar src={who?.avatar_url ?? null} radius="xl" size="lg" alt={who?.name ?? ""} />
          <Stack gap={2}>
            <Text size="sm" fw={500}>
              账号状态
            </Text>
            {who !== null &&
              (who.url !== null ? (
                <Anchor href={who.url} target="_blank" rel="noreferrer" size="sm">
                  {who.name}
                </Anchor>
              ) : (
                <Text size="sm">{who.name}</Text>
              ))}
            {plugin.logged_in && account.isFetching && who === null && (
              <Text size="sm" c="dimmed">
                正在读取账号…
              </Text>
            )}
            {account.data?.error != null && (
              <Text size="sm" c="red">
                {account.data.error}
              </Text>
            )}
            <Text size="xs" c="dimmed">
              {detail}
            </Text>
          </Stack>
        </Group>
        <Group gap="xs" wrap="nowrap">
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
      {choosing && (
        <LoginModal plugin={plugin} onChanged={onChanged} onClose={() => setChoosing(false)} />
      )}
    </Paper>
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
              <Text size="xs" c="dimmed">
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
