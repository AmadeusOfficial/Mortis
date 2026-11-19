import { Alert, Anchor, Select, Stack, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { Link } from "react-router";
import { type PluginInfo, providesAlone } from "../../api";
import styles from "../../pages/SettingsPage.module.css";
import { PluginSettingsCard, usePlugins } from "../PluginsSection";
import { Pill } from "../ui";
import { SaveBar, SettingCard, SettingRow } from "./SettingLayout";
import { useSection } from "./GeneralSections";

const AUTO = "";

function statusText(plugin: PluginInfo): string {
  if (!plugin.enabled) {
    return "已停用";
  }
  if (plugin.error !== null) {
    return "加载失败";
  }
  return plugin.available === true ? "可用" : `不可用：${plugin.notice ?? "原因未知"}`;
}

function isReady(plugin: PluginInfo): boolean {
  return plugin.enabled && plugin.error === null && plugin.available === true;
}

// 这一页只管视频播放器；启动游戏的播放插件（如 Galgame 启动）放在它所属数据类型的设置页。
export function playsVideo(plugin: PluginInfo): boolean {
  return providesAlone(plugin, "player") && (plugin.file_kinds ?? ["video"]).includes("video");
}

// 播放：选默认的播放插件，各播放插件的路径设置和可用状态。只调用本机播放器，不在网页里播。
export function PlaybackSection() {
  const queryClient = useQueryClient();
  const { plugins } = usePlugins();
  const { saved, draft, setDraft, save, settings } = useSection("playback", "播放设置");
  const selectId = useId();
  const hintId = useId();
  const players = (plugins.data ?? []).filter((plugin) => providesAlone(plugin, "player"));
  const videoPlayers = players.filter(playsVideo);

  if (settings.isError || plugins.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {(settings.error ?? plugins.error)?.message}
      </Alert>
    );
  }
  if (draft === null || saved === null || plugins.isPending) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }

  const chosen = videoPlayers.find((plugin) => plugin.id === draft.player) ?? null;
  const autoPick = videoPlayers.find(isReady) ?? null;
  const dirtyCount = draft.player !== saved.player ? 1 : 0;
  const options = [
    { value: AUTO, label: `自动（第一个可用的${autoPick !== null ? `：${autoPick.name}` : ""}）` },
    ...videoPlayers.map((plugin) => ({
      value: plugin.id,
      label: isReady(plugin) ? plugin.name : `${plugin.name}（${statusText(plugin)}）`,
    })),
  ];
  if (draft.player !== AUTO && chosen === null) {
    options.push({ value: draft.player, label: `${draft.player}（没有安装）` });
  }
  const effective = draft.player === AUTO ? autoPick : chosen;
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["plugins"] });

  return (
    <Stack gap="md">
      <SettingCard
        title="播放"
        description="点「播放」时用本机播放器打开视频文件，不在网页里播放，也不转码。"
        actions={
          <Pill tone={effective !== null && isReady(effective) ? "ok" : "warn"}>
            {effective === null ? "没有可用的播放器" : statusText(effective)}
          </Pill>
        }
      >
        <div className={styles.fieldList}>
          <SettingRow
            label="默认播放器"
            hint="选了某个播放器但它不可用时，播放会报出原因，不会换成别的。"
            htmlFor={selectId}
            hintId={hintId}
          >
            <Select
              id={selectId}
              aria-describedby={hintId}
              maw={360}
              allowDeselect={false}
              value={draft.player}
              data={options}
              onChange={(value) => value !== null && setDraft({ player: value })}
            />
          </SettingRow>
        </div>
      </SettingCard>

      {videoPlayers.length === 0 && (
        <Alert color="yellow" title="没有播放插件">
          插件目录里没有播放插件。可以在
          <Anchor component={Link} to="/settings?section=plugins">
            插件管理
          </Anchor>
          里重新加载。
        </Alert>
      )}

      {videoPlayers.map((plugin) => (
        <PlayerCard key={plugin.id} plugin={plugin} onChanged={refresh} />
      ))}

      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel="保存播放设置"
        saving={save.isPending}
        error={save.error}
        onDiscard={() => {
          setDraft(saved);
          save.reset();
        }}
        onSave={() => save.mutate(draft)}
      />
    </Stack>
  );
}

export function PlayerCard({ plugin, onChanged }: { plugin: PluginInfo; onChanged: () => void }) {
  const hasSettings = Object.keys(plugin.settings_schema?.properties ?? {}).length > 0;
  return (
    <>
      <SettingCard
        title={plugin.name}
        actions={<Pill tone={isReady(plugin) ? "ok" : "warn"}>{statusText(plugin)}</Pill>}
      >
        <div className={styles.fieldList}>
          {plugin.detected_path !== null && (
            <SettingRow label="会启动的程序" hint="填了路径就用填的；留空时是自动找到的">
              <Text size="sm" className="mo-mono" aria-label={`${plugin.name} 会启动的程序`}>
                {plugin.detected_path}
              </Text>
            </SettingRow>
          )}
          {plugin.detected_path === null && plugin.notice !== null && (
            <div className={styles.cardBody}>
              <Alert color="yellow">{plugin.notice}</Alert>
            </div>
          )}
          {plugin.error !== null && (
            <div className={styles.cardBody}>
              <Alert color="red" title="加载失败">
                {plugin.error}
              </Alert>
            </div>
          )}
          {!hasSettings && plugin.error === null && (
            <div className={styles.cardBody}>
              <Text size="sm" c="var(--mo-dim)">
                没有可改的设置。
              </Text>
            </div>
          )}
        </div>
      </SettingCard>
      {hasSettings && plugin.enabled && plugin.error === null && (
        <PluginSettingsCard
          plugin={plugin}
          onChanged={onChanged}
          title={`${plugin.name} 设置`}
          saveLabel={`保存 ${plugin.name} 设置`}
        />
      )}
    </>
  );
}
