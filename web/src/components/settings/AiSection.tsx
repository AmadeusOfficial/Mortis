import { Alert, Button, NumberInput, Stack, Text, TextInput } from "@mantine/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { type AiConfig, checkAi, fetchSecretsStatus } from "../../api";
import styles from "../../pages/SettingsPage.module.css";
import { Pill } from "../ui";
import { SecretField, useSection } from "./GeneralSections";
import { SaveBar, SettingCard, SettingRow } from "./SettingLayout";

export function AiSection() {
  const { saved, draft: ai, setDraft, save, settings } = useSection("ai", "AI 设置");
  const status = useQuery({ queryKey: ["secrets"], queryFn: fetchSecretsStatus });
  const check = useMutation({ mutationFn: checkAi });
  const [rounds, setRounds] = useState<number | "">("");
  const urlId = useId();
  const urlHintId = useId();
  const modelId = useId();
  const modelHintId = useId();
  const roundsId = useId();
  const roundsHintId = useId();

  const loadedRounds = ai?.max_rounds;
  useEffect(() => {
    if (loadedRounds !== undefined) {
      setRounds(loadedRounds);
    }
  }, [loadedRounds]);

  if (settings.isError) {
    return (
      <Alert color="red" title="读不到设置">
        {settings.error.message}
      </Alert>
    );
  }
  if (ai === null || saved === null) {
    return <Text c="var(--mo-dim)">正在读取设置…</Text>;
  }

  const filled = status.data?.ai_api_key ?? false;
  const urlInvalid = !/^https?:\/\/\S+$/.test(ai.base_url.trim());
  const modelInvalid = ai.model.trim() === "";
  const roundsInvalid = rounds === "" || rounds < 1 || rounds > 20;
  const setAi = (patch: Partial<AiConfig>) => {
    setDraft({ ...ai, ...patch });
    check.reset();
  };

  let dirtyCount = 0;
  if (ai.base_url !== saved.base_url) dirtyCount += 1;
  if (ai.model !== saved.model) dirtyCount += 1;
  if (rounds !== saved.max_rounds) dirtyCount += 1;

  const discard = () => {
    setDraft(saved);
    setRounds(saved.max_rounds);
    save.reset();
    check.reset();
  };

  return (
    <Stack gap="md">
      <SettingCard
        title="模型接口"
        description="智能体用的模型，OpenAI 兼容的 Chat Completions 接口，保存后即用"
        actions={
          <Pill tone={status.data === undefined ? "neutral" : filled ? "ok" : "warn"}>
            {status.data === undefined ? "正在读取…" : filled ? "Key 已填" : "Key 未填"}
          </Pill>
        }
      >
        <div className={styles.fieldList}>
          <SettingRow
            label="接口地址"
            hint="请求发到这个地址下的 /chat/completions，例如 https://api.deepseek.com"
            htmlFor={urlId}
            hintId={urlHintId}
          >
            <TextInput
              id={urlId}
              aria-describedby={urlHintId}
              className={styles.monoInput}
              maw={420}
              value={ai.base_url}
              onChange={(event) => setAi({ base_url: event.currentTarget.value })}
              error={urlInvalid ? "接口地址要以 http:// 或 https:// 开头" : undefined}
            />
          </SettingRow>
          <SettingRow
            label="模型名"
            hint="按接口文档填写，要支持工具调用，例如 deepseek-flash"
            htmlFor={modelId}
            hintId={modelHintId}
          >
            <TextInput
              id={modelId}
              aria-describedby={modelHintId}
              className={styles.monoInput}
              maw={340}
              value={ai.model}
              onChange={(event) => setAi({ model: event.currentTarget.value })}
              error={modelInvalid ? "模型名不能为空" : undefined}
            />
          </SettingRow>
          <SettingRow
            label="最多工具轮数"
            hint="一次提问最多调用几轮工具，1 到 20"
            htmlFor={roundsId}
            hintId={roundsHintId}
          >
            <NumberInput
              id={roundsId}
              aria-describedby={roundsHintId}
              className={styles.monoInput}
              maw={160}
              min={1}
              max={20}
              allowDecimal={false}
              value={rounds}
              onChange={(value) => setRounds(typeof value === "number" ? value : "")}
              error={roundsInvalid ? "要在 1 到 20 之间" : undefined}
            />
          </SettingRow>
          <SecretField
            secretKey="ai_api_key"
            label="API Key"
            hintExtra="只存在密钥文件里，页面上不显示。"
            onChanged={() => check.reset()}
          />
        </div>
        <div className={styles.cardBody} style={{ borderTop: "1px solid var(--mo-line)" }}>
          <Stack gap="sm">
            <div>
              <Button
                variant="default"
                loading={check.isPending}
                disabled={urlInvalid || modelInvalid}
                onClick={() => check.mutate({ base_url: ai.base_url, model: ai.model })}
              >
                测试连接
              </Button>
            </div>
            <Text size="xs" c="var(--mo-dim)">
              用上面填的接口地址和模型名、已保存的 key 和网络设置，发一句很短的请求。
            </Text>
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

      <SaveBar
        dirtyCount={dirtyCount}
        saveLabel="保存 AI 设置"
        saving={save.isPending}
        disabled={urlInvalid || modelInvalid || roundsInvalid}
        error={save.error}
        onDiscard={discard}
        onSave={() => {
          if (rounds !== "") {
            save.mutate({ ...ai, max_rounds: rounds });
          }
        }}
      />
    </Stack>
  );
}
