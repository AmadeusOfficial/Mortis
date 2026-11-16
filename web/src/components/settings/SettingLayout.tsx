import { Alert, Button, Group, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import styles from "../../pages/SettingsPage.module.css";

export function SettingCard({
  title,
  description,
  actions,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={styles.card}>
      <div className={styles.cardHeader}>
        <div className={styles.cardHeaderMeta}>
          <Title order={4} fz={15} lh={1.35}>
            {title}
          </Title>
          {description !== undefined && description !== null && description !== "" && (
            <Text className={styles.cardDesc}>{description}</Text>
          )}
        </div>
        {actions && (
          <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
            {actions}
          </Group>
        )}
      </div>
      {children}
    </section>
  );
}

export function SettingRow({
  label,
  hint,
  htmlFor,
  hintId,
  align = "center",
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  htmlFor?: string;
  hintId?: string;
  align?: "center" | "start";
  children: ReactNode;
}) {
  return (
    <div className={styles.fieldRow} data-align={align}>
      <div className={styles.fieldMeta}>
        {htmlFor !== undefined ? (
          <label htmlFor={htmlFor} className={styles.fieldTitle} data-clickable="true">
            {label}
          </label>
        ) : (
          <div className={styles.fieldTitle}>{label}</div>
        )}
        {hint !== undefined && hint !== null && hint !== "" && (
          <div id={hintId} className={styles.fieldHint}>
            {hint}
          </div>
        )}
      </div>
      <div className={styles.fieldControl}>{children}</div>
    </div>
  );
}

export function SaveBar({
  dirtyCount,
  saveLabel,
  saving,
  disabled,
  error,
  onDiscard,
  onSave,
}: {
  dirtyCount: number;
  saveLabel: string;
  saving: boolean;
  disabled?: boolean;
  error: Error | null;
  onDiscard: () => void;
  onSave: () => void;
}) {
  if (dirtyCount <= 0 && error === null) {
    return null;
  }

  return (
    <div className={styles.saveBar} role="region" aria-label="未保存的更改">
      {error !== null && (
        <Alert color="red" title="保存失败">
          {error.message}
        </Alert>
      )}
      <div className={styles.saveBarRow}>
        <div className={styles.saveBarMsg}>
          <span className={styles.saveBarDot} aria-hidden="true" />
          <span>
            {dirtyCount > 0 ? `有 ${dirtyCount} 处改动未保存` : "有改动未保存"}
          </span>
        </div>
        <Group gap="xs" wrap="nowrap">
          <Button variant="subtle" size="sm" disabled={saving} onClick={onDiscard}>
            放弃
          </Button>
          <Button
            variant="filled"
            size="sm"
            loading={saving}
            disabled={disabled}
            onClick={onSave}
          >
            {saveLabel}
          </Button>
        </Group>
      </div>
    </div>
  );
}
