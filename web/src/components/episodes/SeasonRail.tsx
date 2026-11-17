import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router";
import type { EpisodeInfo, SeasonInfo } from "../../api";
import { IconBack, IconChevronRight } from "../../icons";
import classes from "./SeasonRail.module.css";

export interface SeasonRailsProps {
  seasons: SeasonInfo[];
  renderEpisodeAction?: (episode: EpisodeInfo) => ReactNode;
}

interface VelocitySample {
  x: number;
  t: number;
}

function formatSeasonHeading(season: SeasonInfo): string {
  if (season.title.trim() !== "") {
    return season.title;
  }
  if (season.number === 0) {
    return "特别篇";
  }
  return season.number !== null ? `第 ${season.number} 季` : "";
}

function formatEpisodeLabel(episode: EpisodeInfo): string {
  return episode.number !== null
    ? `${episode.number}. ${episode.title}`
    : episode.title;
}

function formatEpisodeAriaLabel(episode: EpisodeInfo): string {
  return episode.number !== null
    ? `第 ${episode.number} 集 ${episode.title}`
    : episode.title;
}

function SeasonRailRow({
  season,
  renderEpisodeAction,
}: {
  season: SeasonInfo;
  renderEpisodeAction?: (episode: EpisodeInfo) => ReactNode;
}) {
  const navigate = useNavigate();
  const trackRef = useRef<HTMLDivElement>(null);
  const [canPrev, setCanPrev] = useState(false);
  const [canNext, setCanNext] = useState(false);
  const [dragging, setDragging] = useState(false);

  // 拖动超过阈值后屏蔽松手时紧随其后的 click，避免误跳转。
  const suppressClickRef = useRef(false);
  const momentumRafRef = useRef<number | null>(null);

  const updateNavState = useCallback(() => {
    const el = trackRef.current;
    if (!el) {
      return;
    }
    const maxScroll = el.scrollWidth - el.clientWidth;
    setCanPrev(el.scrollLeft > 2);
    setCanNext(maxScroll > 2 && el.scrollLeft < maxScroll - 2);
  }, []);

  useEffect(() => {
    updateNavState();
    const el = trackRef.current;
    if (!el) {
      return;
    }
    const observer = new ResizeObserver(() => {
      updateNavState();
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, [season.episodes.length, updateNavState]);

  useEffect(() => {
    return () => {
      if (momentumRafRef.current !== null) {
        cancelAnimationFrame(momentumRafRef.current);
      }
    };
  }, []);

  const scrollByPage = (direction: -1 | 1) => {
    const el = trackRef.current;
    if (!el) {
      return;
    }
    if (momentumRafRef.current !== null) {
      cancelAnimationFrame(momentumRafRef.current);
      momentumRafRef.current = null;
    }
    const step = Math.max(el.clientWidth, 220);
    el.scrollBy({ left: direction * step, behavior: "smooth" });
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== "mouse" || e.button !== 0) {
      return;
    }
    const el = trackRef.current;
    if (!el) {
      return;
    }
    if (momentumRafRef.current !== null) {
      cancelAnimationFrame(momentumRafRef.current);
      momentumRafRef.current = null;
    }

    const startX = e.clientX;
    const startScrollLeft = el.scrollLeft;
    let hasDragged = false;
    const samples: VelocitySample[] = [{ x: e.clientX, t: performance.now() }];

    const onMove = (moveEvent: PointerEvent) => {
      const dx = moveEvent.clientX - startX;
      if (!hasDragged && Math.abs(dx) > 6) {
        hasDragged = true;
        suppressClickRef.current = true;
        setDragging(true);
      }
      if (!hasDragged) {
        return;
      }
      el.scrollLeft = startScrollLeft - dx;
      const now = performance.now();
      samples.push({ x: moveEvent.clientX, t: now });
      while (samples.length > 2 && now - samples[0].t > 90) {
        samples.shift();
      }
    };

    const cleanup = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };

    const onUp = () => {
      cleanup();
      if (!hasDragged) {
        return;
      }

      // 计算最近一段滑动的初速度（px/ms），松手后做惯性减速。
      const now = performance.now();
      const recent = samples.filter((s) => now - s.t <= 90);
      const first = recent[0] ?? samples[0];
      const last = recent[recent.length - 1] ?? samples[samples.length - 1];
      const dt = last.t - first.t;
      let velocity = dt > 4 ? (first.x - last.x) / dt : 0;

      if (Math.abs(velocity) > 0.15) {
        let prevTime = performance.now();
        const step = (frameTime: number) => {
          const frameDt = Math.min(frameTime - prevTime, 32);
          prevTime = frameTime;
          el.scrollLeft += velocity * frameDt;
          velocity *= Math.pow(0.94, frameDt / 16);

          const maxScroll = el.scrollWidth - el.clientWidth;
          const atEdge = el.scrollLeft <= 0 || el.scrollLeft >= maxScroll;
          if (Math.abs(velocity) < 0.04 || atEdge) {
            momentumRafRef.current = null;
            setDragging(false);
            return;
          }
          momentumRafRef.current = requestAnimationFrame(step);
        };
        momentumRafRef.current = requestAnimationFrame(step);
      } else {
        setDragging(false);
      }

      window.setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  const handleClickCapture = (e: React.MouseEvent<HTMLDivElement>) => {
    if (suppressClickRef.current) {
      e.preventDefault();
      e.stopPropagation();
      suppressClickRef.current = false;
    }
  };

  const heading = formatSeasonHeading(season);

  return (
    <section className={classes.section} aria-label={heading}>
      <div className={classes.header}>
        <div className={classes.titleGroup}>
          <div className={classes.titleWrap}>
            <h3 className={classes.title}>{heading}</h3>
            <span className={classes.titleBar} aria-hidden="true" />
          </div>
          <span className={`${classes.meta} mo-mono`}>
            {season.episode_count} 集 · 有文件 {season.file_count}
          </span>
        </div>

        <div className={classes.controls}>
          <button
            type="button"
            className={classes.navBtn}
            aria-label={`${heading} 上一页`}
            disabled={!canPrev}
            onClick={() => scrollByPage(-1)}
          >
            <IconBack size={16} />
          </button>
          <button
            type="button"
            className={classes.navBtn}
            aria-label={`${heading} 下一页`}
            disabled={!canNext}
            onClick={() => scrollByPage(1)}
          >
            <IconChevronRight size={16} />
          </button>
        </div>
      </div>

      <div
        ref={trackRef}
        className={
          dragging ? `${classes.track} ${classes.trackDragging}` : classes.track
        }
        onScroll={updateNavState}
        onPointerDown={handlePointerDown}
        onClickCapture={handleClickCapture}
        onDragStart={(e) => e.preventDefault()}
      >
        {season.episodes.map((episode) => {
          const epHref = `/items/${episode.id}`;
          return (
            <div
              key={episode.id}
              className={classes.card}
              data-missing={episode.has_file ? undefined : "true"}
            >
              <a
                href={epHref}
                className={classes.cardLink}
                aria-label={formatEpisodeAriaLabel(episode)}
                draggable={false}
                onClick={(e) => {
                  e.preventDefault();
                  void navigate(epHref);
                }}
              >
                <div className={classes.thumb}>
                  {episode.still_url !== null ? (
                    <img
                      src={episode.still_url}
                      alt={episode.title}
                      className={classes.thumbImg}
                      loading="lazy"
                      draggable={false}
                    />
                  ) : (
                    <div className={classes.thumbBlank}>
                      <span className={`${classes.blankNum} mo-mono`}>
                        {episode.number !== null
                          ? String(episode.number).padStart(2, "0")
                          : "—"}
                      </span>
                    </div>
                  )}

                  {!episode.has_file && (
                    <span className={classes.missingBadge}>缺</span>
                  )}
                </div>

                <div className={classes.info}>
                  <div className={classes.epTitle}>
                    {formatEpisodeLabel(episode)}
                  </div>
                  <div className={`${classes.epDate} mo-mono`}>
                    {episode.air_date ?? "—"}
                  </div>
                </div>
              </a>
              {renderEpisodeAction !== undefined && (
                <div className={classes.actionSlot}>
                  <div className={classes.actionInner}>
                    {renderEpisodeAction(episode)}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function SeasonRails({
  seasons,
  renderEpisodeAction,
}: SeasonRailsProps) {
  const sortedSeasons = useMemo(() => {
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
  }, [seasons]);

  if (sortedSeasons.length === 0) {
    return null;
  }

  return (
    <div className={classes.rails}>
      {sortedSeasons.map((season) => (
        <SeasonRailRow
          key={season.id}
          season={season}
          renderEpisodeAction={renderEpisodeAction}
        />
      ))}
    </div>
  );
}
