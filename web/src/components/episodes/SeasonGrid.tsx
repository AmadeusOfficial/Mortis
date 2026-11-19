import { useMemo } from "react";
import { useNavigate } from "react-router";
import type { EpisodeInfo, SeasonInfo } from "../../api";
import { IconPlay } from "../../icons";
import classes from "./SeasonGrid.module.css";

export interface SeasonGridProps {
  seasons: SeasonInfo[];
  showMissing: boolean;
  playingId?: number | null;
  onPlayEpisode?: (episode: EpisodeInfo) => void;
}

export function formatSeasonHeading(season: SeasonInfo): string {
  if (season.title.trim() !== "") {
    return season.title;
  }
  if (season.number === 0) {
    return "特别篇";
  }
  return season.number !== null ? `第 ${season.number} 季` : "";
}

export function sortSeasons(seasons: SeasonInfo[]): SeasonInfo[] {
  return [...seasons].sort((a, b) => {
    const aSpecial = a.number === 0;
    const bSpecial = b.number === 0;
    if (aSpecial !== bSpecial) {
      return aSpecial ? 1 : -1;
    }
    const aNum = a.number ?? Number.MAX_SAFE_INTEGER;
    const bNum = b.number ?? Number.MAX_SAFE_INTEGER;
    return aNum - bNum;
  });
}

export function SeasonGrid({
  seasons,
  showMissing,
  playingId,
  onPlayEpisode,
}: SeasonGridProps) {
  const navigate = useNavigate();

  const visibleSeasons = useMemo(() => {
    const sorted = sortSeasons(seasons);
    if (showMissing) {
      return sorted;
    }
    return sorted.filter((season) => season.file_count > 0);
  }, [seasons, showMissing]);

  if (visibleSeasons.length === 0) {
    return null;
  }

  return (
    <div className={classes.grid}>
      {visibleSeasons.map((season) => {
        const heading = formatSeasonHeading(season);
        const allMissing = season.file_count === 0;
        const badgeCount = showMissing ? season.episode_count : season.file_count;
        const firstPlayable = season.episodes.find((ep) => ep.playable);
        const href = `/items/${season.id}`;
        const badgeLabel =
          season.number === 0
            ? "SPECIALS"
            : season.number !== null
              ? `SEASON ${String(season.number).padStart(2, "0")}`
              : "SEASON";

        return (
          <div
            key={season.id}
            className={`${classes.card} ${allMissing ? classes.allMissing : ""}`}
          >
            <div className={classes.frame}>
              <a
                href={href}
                className={classes.link}
                aria-label={heading}
                onClick={(e) => {
                  e.preventDefault();
                  void navigate(href);
                }}
              >
                {season.poster_url !== null ? (
                  <img
                    src={season.poster_url}
                    alt={heading}
                    className={classes.posterImg}
                    loading="lazy"
                  />
                ) : (
                  <div className={classes.posterBlank}>
                    <span className={`${classes.blankBadge} mo-mono`}>
                      {badgeLabel}
                    </span>
                    <span className={classes.blankTitle}>{heading}</span>
                  </div>
                )}

                <span
                  className={`${classes.countBadge} ${
                    allMissing ? classes.countMissing : classes.countHave
                  } mo-mono`}
                  title={
                    allMissing
                      ? "暂无本地文件"
                      : `已有 ${season.file_count} / ${season.episode_count} 集`
                  }
                >
                  {badgeCount}
                </span>
              </a>

              {firstPlayable !== undefined && onPlayEpisode !== undefined && (
                <button
                  type="button"
                  className={classes.playOverlayBtn}
                  aria-label={`播放 ${heading} 第 ${firstPlayable.number ?? "?"} 集`}
                  disabled={playingId === firstPlayable.id}
                  onClick={() => onPlayEpisode(firstPlayable)}
                >
                  <IconPlay size={20} />
                </button>
              )}
            </div>

            <a
              href={href}
              className={classes.caption}
              onClick={(e) => {
                e.preventDefault();
                void navigate(href);
              }}
            >
              {heading}
            </a>
          </div>
        );
      })}
    </div>
  );
}
