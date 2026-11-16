import { Button } from "@mantine/core";
import { useState } from "react";
import { IconImage } from "../../icons";
import { SourceBadge } from "../ui";
import classes from "./MatchPanel.module.css";

export interface MatchCandidateProps {
  title: string;
  originalTitle?: string | null;
  year: number | null;
  source: string;
  overview?: string | null;
  posterUrl: string | null;
  score?: number | null;
  matchThreshold?: number;
  isFirst: boolean;
  embedded?: boolean;
  loading: boolean;
  disabled: boolean;
  pickAriaLabel?: string;
  onPick: () => void;
}

export function MatchCandidate({
  title,
  originalTitle,
  year,
  source,
  overview,
  posterUrl,
  score,
  matchThreshold = 0.75,
  isFirst,
  embedded = false,
  loading,
  disabled,
  pickAriaLabel,
  onPick,
}: MatchCandidateProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const showPoster = posterUrl !== null && posterUrl !== "" && !imgFailed;

  const metaParts: string[] = [];
  if (year !== null) {
    metaParts.push(String(year));
  }
  if (
    originalTitle !== undefined &&
    originalTitle !== null &&
    originalTitle.trim() !== "" &&
    originalTitle !== title
  ) {
    metaParts.push(originalTitle);
  }

  const hasOverview = overview !== undefined && overview !== null && overview.trim() !== "";
  const hasScore = typeof score === "number" && Number.isFinite(score);

  return (
    <div
      className={[
        classes.cand,
        !embedded ? classes.candModal : "",
        isFirst ? classes.candBest : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className={classes.posterWrap}>
        {showPoster ? (
          <img
            className={classes.posterImg}
            src={posterUrl}
            alt={title}
            referrerPolicy="no-referrer"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <div className={classes.posterPlaceholder} aria-hidden="true">
            <IconImage size={18} />
          </div>
        )}
      </div>

      <div className={classes.candBody}>
        <div className={classes.candTitle}>{title}</div>
        <div className={classes.candMeta}>
          {metaParts.length > 0 && <span>{metaParts.join(" · ")}</span>}
          <SourceBadge source={source} />
        </div>
        {hasOverview && <p className={classes.candOverview}>{overview}</p>}
      </div>

      <div className={classes.candRight}>
        {hasScore && (
          <span
            className={`${classes.score} mo-mono`}
            data-tone={score >= matchThreshold ? "ok" : "warn"}
          >
            {score.toFixed(2)}
          </span>
        )}
        <Button
          size="xs"
          variant={isFirst ? "filled" : "default"}
          loading={loading}
          disabled={disabled}
          aria-label={pickAriaLabel}
          onClick={onPick}
        >
          选这个
        </Button>
      </div>
    </div>
  );
}
