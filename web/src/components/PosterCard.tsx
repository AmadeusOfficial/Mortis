import { Link } from "react-router";
import type { WallItem } from "../api";
import { IconImage, IconScrape, IconSearch, IconWarning } from "../icons";
import styles from "./PosterCard.module.css";

export interface PosterCardProps {
  item: WallItem;
  className?: string;
}

export function PosterCard({ item, className }: PosterCardProps) {
  const hasIssue = typeof item.issue === "string" && item.issue !== "";
  const state = hasIssue ? "issue" : !item.is_scraped ? "unscraped" : "normal";
  const caption = [item.year, item.subtitle]
    .filter((part) => part !== null && part !== "")
    .join(" · ");

  return (
    <Link
      to={`/items/${item.id}`}
      className={[styles.card, className].filter(Boolean).join(" ")}
      data-state={state}
    >
      <div className={styles.poster}>
        {item.poster_url !== null ? (
          <img
            className={styles.posterImg}
            src={item.poster_url}
            alt={item.title}
            loading="lazy"
          />
        ) : (
          <div className={styles.posterBlank}>
            <IconImage size={24} className={styles.blankIcon} />
            <span className={styles.blankTitle}>{item.title}</span>
          </div>
        )}
        <div className={styles.scrim} />
        {hasIssue ? (
          <span className={`${styles.badge} ${styles.badgeBad}`}>
            <IconWarning size={11} />
            异常
          </span>
        ) : (
          !item.is_scraped && (
            <span className={`${styles.badge} ${styles.badgeWarn}`}>
              <IconScrape size={11} />
              未刮削
            </span>
          )
        )}
        <div className={styles.overlay} aria-hidden="true">
          <span className={styles.detailBtn}>
            <IconSearch size={12} />
            详情
          </span>
        </div>
      </div>

      <h3 className={styles.title} title={item.title}>
        {item.title}
      </h3>
      <p className={styles.subtitle} title={caption || undefined}>
        {caption || "\u00a0"}
      </p>

      {hasIssue && (
        <div className={`${styles.note} ${styles.noteBad}`} title={item.issue ?? undefined}>
          <span className={styles.noteText}>{item.issue}</span>
        </div>
      )}
      {!hasIssue && !item.is_scraped && (
        <div className={`${styles.note} ${styles.noteWarn}`} title="待刮削元数据">
          <span className={styles.noteText}>待刮削元数据</span>
        </div>
      )}
    </Link>
  );
}
