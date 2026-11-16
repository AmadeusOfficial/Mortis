import { Modal } from "@mantine/core";
import { type SourceHit } from "../api";
import { SourceSearchPanel } from "./match/SourceSearchPanel";

// 插件数据类型的匹配：先选来源，再在这个来源里按名字搜。
export function SourceSearchModal({
  title,
  dataType,
  initialQuery,
  exclude = [],
  picking,
  error,
  onPick,
  onClose,
}: {
  title: string;
  dataType: string;
  initialQuery: string;
  // 不列出的来源 id，比如补充来源时的主来源。
  exclude?: string[];
  picking: boolean;
  error: string | null;
  onPick: (source: string, hit: SourceHit) => void;
  onClose: () => void;
}) {
  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <SourceSearchPanel
        dataType={dataType}
        initialQuery={initialQuery}
        exclude={exclude}
        picking={picking}
        error={error}
        autoFocus
        onPick={onPick}
      />
    </Modal>
  );
}
