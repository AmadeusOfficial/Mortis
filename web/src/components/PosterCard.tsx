import type { MouseEvent } from "react";
import { Link } from "react-router";
import type { WallItem } from "../api";
import { CoverCollage } from "./CoverCollage";
import { IconCheck, IconImage, IconScrape, IconWarning } from "../icons";
import styles from "./PosterCard.module.css";

export interface PosterCardProps {
  item: WallItem;
  className?: string;
  selecting?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (event: MouseEvent) => void;
  onQuickSelect?: (event: MouseEvent) => void;
}

export function PosterCard({
  item,
  className,
  selecting = false,
  selectable = true,
  selected = false,
  onToggle,
  onQuickSelect,
}: PosterCardProps) {
  const hasIssue = typeof item.issue === "string" && item.issue !== "";
  const state =
    item.kind === "collection"
      ? hasIssue
        ? "issue"
        : "normal"
      : hasIssue
        ? "issue"
        : !item.is_scraped && !item.is_manual
          ? "unscraped"
          : "normal";
  const caption = [item.year, item.subtitle]
    .filter((part) => part !== null && part !== "")
    .join(" · ");

  const collage = item.cover_mode === "auto" || item.cover_mode === "grid";
  const face = collage ? (
    <CoverCollage urls={item.cover_urls ?? []} />
  ) : item.poster_url !== null ? (
    <img className={styles.posterImg} src={item.poster_url} alt={item.title} loading="lazy" />
  ) : (
    <div className={styles.posterBlank}>
      <IconImage size={24} className={styles.blankIcon} />
      <span className={styles.blankTitle}>{item.title}</span>
    </div>
  );
  const classNameText = [styles.card, className].filter(Boolean).join(" ");
  const body = (
    <>
      <div className={styles.poster}>
        {face}
        {selecting && selectable && (
          <span className={styles.check} data-on={selected ? "1" : "0"} aria-hidden="true">
            {selected && <IconCheck size={12} strokeWidth={2.6} />}
          </span>
        )}
        <div className={styles.scrim} />
        {state === "issue" && (
          <span className={`${styles.badge} ${styles.badgeBad}`}>
            <IconWarning size={12} />
            异常
          </span>
        )}
        {state === "unscraped" && (
          <span className={`${styles.badge} ${styles.badgeWarn}`}>
            <IconScrape size={12} />
            未刮削
          </span>
        )}
      </div>

      <div className={styles.meta}>
        <h3 className={styles.title} title={item.title}>
          {item.title}
        </h3>
        {state === "issue" ? (
          <p className={`${styles.subtitle} ${styles.statusBad}`} title={item.issue ?? undefined}>
            {item.issue}
          </p>
        ) : state === "unscraped" ? (
          <p className={`${styles.subtitle} ${styles.statusWarn}`}>待刮削元数据</p>
        ) : (
          <p className={styles.subtitle} title={caption || undefined}>
            {caption || "\u00a0"}
          </p>
        )}
      </div>
    </>
  );

  if (selecting) {
    return (
      <button
        type="button"
        className={classNameText}
        data-state={state}
        data-selected={selected ? "1" : "0"}
        data-disabled={selectable ? undefined : "1"}
        disabled={!selectable}
        aria-pressed={selectable ? selected : undefined}
        onClick={selectable ? onToggle : undefined}
      >
        {body}
      </button>
    );
  }

  if (!selecting && selectable && onQuickSelect !== undefined) {
    return (
      <div className={classNameText} data-state={state}>
        <Link to={`/items/${item.id}`} className={styles.cardLink}>
          {body}
        </Link>
        <button
          type="button"
          className={`${styles.check} ${styles.quickCheck}`}
          aria-label={`选中 ${item.title}`}
          onClick={onQuickSelect}
        />
      </div>
    );
  }

  return (
    <Link to={`/items/${item.id}`} className={classNameText} data-state={state}>
      {body}
    </Link>
  );
}
