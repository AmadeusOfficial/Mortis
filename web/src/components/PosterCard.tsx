import { Link } from "react-router";
import type { WallItem } from "../api";
import { IconImage, IconScrape, IconWarning } from "../icons";
import styles from "./PosterCard.module.css";

export interface PosterCardProps {
  item: WallItem;
  className?: string;
}

export function PosterCard({ item, className }: PosterCardProps) {
  const hasIssue = typeof item.issue === "string" && item.issue !== "";
  const state = hasIssue
    ? "issue"
    : !item.is_scraped && !item.is_manual
      ? "unscraped"
      : "normal";
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
    </Link>
  );
}
