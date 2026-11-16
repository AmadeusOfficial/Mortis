import {
  Alert,
  Badge,
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
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type DataType,
  type JsonSchema,
  type JsonSchemaProperty,
  type PluginInfo,
  fetchDataTypes,
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

export function PluginsSection({
  loginPluginId,
  onLoginHandled,
}: {
  // 后端登录成功后跳回 /settings?plugin=<id>&login=ok，这里只负责提示一次。
  loginPluginId: string | null;
  onLoginHandled: () => void;
}) {
  const queryClient = useQueryClient();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: fetchPlugins });
  const dataTypes = useQuery({ queryKey: ["data-types"], queryFn: fetchDataTypes });
  const anchor = useRef<HTMLDivElement>(null);
  const loginHandled = useRef(false);

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    void queryClient.invalidateQueries({ queryKey: ["data-types"] });
    void queryClient.invalidateQueries({ queryKey: ["libraries"] });
    // 启停来源插件会改变各类型的可用来源，来源顺序要跟着重读。
    void queryClient.invalidateQueries({ queryKey: ["source-orders"] });
  }, [queryClient]);

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
      refresh();
      notifications.show({
        message: saved.enabled ? `已启用 ${saved.name}` : `已停用 ${saved.name}`,
      });
    },
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  useEffect(() => {
    if (loginPluginId === null || loginHandled.current || plugins.data === undefined) {
      return;
    }
    loginHandled.current = true;
    const plugin = plugins.data.find((item) => item.id === loginPluginId);
    anchor.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    notifications.show({ color: "green", message: `${plugin?.name ?? loginPluginId} 登录成功` });
    void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    onLoginHandled();
  }, [loginPluginId, plugins.data, queryClient, onLoginHandled]);

  const typeName = (id: string | null): string => {
    if (id === null) {
      return "";
    }
    const found: DataType | undefined = dataTypes.data?.find((type) => type.id === id);
    return found?.name ?? id;
  };

  return (
    <Paper withBorder p="md" ref={anchor}>
      <Stack>
        <Group justify="space-between">
          <Title order={4}>插件</Title>
          <Button variant="default" loading={reload.isPending} onClick={() => reload.mutate()}>
            重新加载插件
          </Button>
        </Group>
        <Text size="sm" c="dimmed">
          插件从自带的 plugins 目录和数据目录的 plugins 目录加载。停用后不参与识别和刮削，已有的条目保留。
        </Text>
        {plugins.isError && (
          <Alert color="red" title="读不到插件">
            {plugins.error.message}
          </Alert>
        )}
        {plugins.isPending && <Text c="dimmed">正在读取插件…</Text>}
        {plugins.isSuccess && plugins.data.length === 0 && (
          <Text c="dimmed">还没有插件。</Text>
        )}
        {plugins.data?.map((plugin) => (
          <PluginCard
            key={plugin.id}
            plugin={plugin}
            dataTypeName={typeName(plugin.data_type)}
            toggling={toggle.isPending && toggle.variables?.id === plugin.id}
            onToggle={(enabled) => toggle.mutate({ id: plugin.id, enabled })}
            onChanged={refresh}
          />
        ))}
      </Stack>
    </Paper>
  );
}

function PluginCard({
  plugin,
  dataTypeName,
  toggling,
  onToggle,
  onChanged,
}: {
  plugin: PluginInfo;
  dataTypeName: string;
  toggling: boolean;
  onToggle: (enabled: boolean) => void;
  onChanged: () => void;
}) {
  return (
    <Paper withBorder p="sm">
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
                服务的数据类型：{dataTypeName}
              </Text>
            )}
          </Stack>
          <Switch
            label="启用"
            checked={plugin.enabled}
            disabled={toggling}
            onChange={(event) => onToggle(event.currentTarget.checked)}
          />
        </Group>
        {plugin.error !== null && (
          <Alert color="red" title="加载失败">
            {plugin.error}
          </Alert>
        )}
        {plugin.notice !== null && <Alert color="yellow">{plugin.notice}</Alert>}
        {plugin.kind === "source" && (
          <>
            <PluginSettingsForm plugin={plugin} onChanged={onChanged} />
            <PluginSecretsForm plugin={plugin} onChanged={onChanged} />
            {plugin.oauth && <PluginOAuth plugin={plugin} onChanged={onChanged} />}
          </>
        )}
      </Stack>
    </Paper>
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
    return null;
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
    return null;
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

function PluginOAuth({ plugin, onChanged }: { plugin: PluginInfo; onChanged: () => void }) {
  const logout = useMutation({
    mutationFn: () => logoutPlugin(plugin.id),
    onSuccess: (saved) => {
      onChanged();
      notifications.show({ color: "green", message: `已退出 ${saved.name}` });
    },
  });
  const canLogin = plugin.secrets?.app_id === true && plugin.secrets?.app_secret === true;
  const redirect = `${window.location.origin}/api/plugins/${plugin.id}/oauth/callback`;
  const status = plugin.logged_in ? "已登录" : "未登录";
  const expiry =
    plugin.expires_at !== null ? `，到期时间 ${formatExpiry(plugin.expires_at)}` : "";

  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        登录
      </Text>
      <Text size="sm">
        {status}
        {expiry}
      </Text>
      <Group>
        {canLogin ? (
          // 登录要整页跳到授权页，不能用 fetch。
          <Button component="a" href={pluginLoginUrl(plugin.id)}>
            登录
          </Button>
        ) : (
          <Button disabled>登录</Button>
        )}
        {plugin.logged_in && (
          <Button variant="default" loading={logout.isPending} onClick={() => logout.mutate()}>
            退出登录
          </Button>
        )}
      </Group>
      {!canLogin && (
        <Text size="xs" c="dimmed">
          先填 App ID 和 App Secret；回调地址填 {redirect}
        </Text>
      )}
      {logout.isError && (
        <Text size="sm" c="red">
          {logout.error.message}
        </Text>
      )}
    </Stack>
  );
}
