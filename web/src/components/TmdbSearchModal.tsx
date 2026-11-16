import { Modal } from "@mantine/core";
import { type MovieHit, type SearchKind } from "../api";
import { TmdbSearchPanel } from "./match/TmdbSearchPanel";

export function TmdbSearchModal({
  title,
  kind = "movie",
  initialQuery,
  initialYear,
  picking,
  error,
  onPick,
  onClose,
}: {
  title: string;
  kind?: SearchKind;
  initialQuery: string;
  initialYear: number | null;
  picking: boolean;
  error: string | null;
  onPick: (hit: MovieHit) => void;
  onClose: () => void;
}) {
  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <TmdbSearchPanel
        kind={kind}
        initialQuery={initialQuery}
        initialYear={initialYear}
        picking={picking}
        error={error}
        autoFocus
        onPick={onPick}
      />
    </Modal>
  );
}
