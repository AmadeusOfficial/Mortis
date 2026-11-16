import { ActionIcon, Group, Tooltip } from "@mantine/core";
import type { FieldOut } from "../../api";
import { IconEdit, IconLock, IconUnlock } from "../../icons";
import { SourceBadge } from "../ui";
import classes from "../../pages/ItemDetailPage.module.css";

// 来源徽标常驻；已锁定的锁图标常驻；编辑和锁定按钮放进 .tools，由外层 .hoverRow 悬停时显示。
export function FieldActions({
  field,
  sourceName,
  lockBusy,
  onEdit,
  onToggleLock,
}: {
  field: FieldOut;
  sourceName: (source: string) => string | undefined;
  lockBusy: boolean;
  onEdit: (field: FieldOut) => void;
  onToggleLock: (field: FieldOut) => void;
}) {
  return (
    <Group gap={4} wrap="nowrap" align="center">
      {field.source !== null && <SourceBadge source={field.source} label={sourceName(field.source)} />}
      {field.locked && field.lockable && (
        <Tooltip label="已锁定，再次刮削不覆盖；点一下解锁" withArrow>
          <ActionIcon
            size="sm"
            variant="subtle"
            color="yellow"
            aria-label={`解锁${field.label}`}
            disabled={lockBusy}
            onClick={() => onToggleLock(field)}
          >
            <IconLock size={14} />
          </ActionIcon>
        </Tooltip>
      )}
      <Group gap={2} wrap="nowrap" className={classes.tools}>
        {field.editable && (
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label={`编辑${field.label}`}
            onClick={() => onEdit(field)}
          >
            <IconEdit size={14} />
          </ActionIcon>
        )}
        {field.lockable && !field.locked && (
          <ActionIcon
            size="sm"
            variant="subtle"
            color="gray"
            aria-label={`锁定${field.label}`}
            disabled={lockBusy}
            onClick={() => onToggleLock(field)}
          >
            <IconUnlock size={14} />
          </ActionIcon>
        )}
      </Group>
    </Group>
  );
}
