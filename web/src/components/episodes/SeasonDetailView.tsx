import { Button } from "@mantine/core";
import { type ReactNode, useMemo } from "react";
import { Link, useNavigate } from "react-router";
import type { EpisodeInfo, ItemDetail } from "../../api";
import {
  IconBack,
  IconChevronRight,
  IconImage,
  IconMore,
  IconPlay,
} from "../../icons";
import { Pill } from "../ui";
import { formatSeasonHeading, sortSeasons } from "./SeasonGrid";
import classes from "./SeasonDetailView.module.css";

export interface SeasonDetailViewProps {
  detail: ItemDetail;
  sidePanel: ReactNode;
  imageSlots?: ReactNode;
  playingId?: number | null;
  onPlayEpisode: (episode: EpisodeInfo) => void;
  onPickPoster?: () => void;
  overviewText?: string | null;
  releaseDate?: string | null;
}

export function SeasonDetailView({
  detail,
  sidePanel,
  imageSlots,
  playingId,
  onPlayEpisode,
  onPickPoster,
  overviewText,
  releaseDate,
}: SeasonDetailViewProps) {
  const navigate = useNavigate();

  const sortedSeasons = useMemo(
    () => sortSeasons(detail.seasons),
    [detail.seasons],
  );

  const currentIdx = sortedSeasons.findIndex((s) => s.id === detail.id);
  const currentSeason =
    currentIdx >= 0 ? sortedSeasons[currentIdx] : undefined;
  const prevSeason = currentIdx > 0 ? sortedSeasons[currentIdx - 1] : undefined;
  const nextSeason =
    currentIdx >= 0 && currentIdx < sortedSeasons.length - 1
      ? sortedSeasons[currentIdx + 1]
      : undefined;

  const episodes = currentSeason?.episodes ?? [];
  const firstPlayable = episodes.find((ep) => ep.playable);

  const rangeChunks = useMemo(() => {
    if (episodes.length <= 24) {
      return [];
    }
    const chunks: { label: string; targetId: number }[] = [];
    for (let i = 0; i < episodes.length; i += 20) {
      const slice = episodes.slice(i, i + 20);
      const startLabel = slice[0].number ?? i + 1;
      const endLabel = slice[slice.length - 1].number ?? i + slice.length;
      chunks.push({
        label: `${startLabel}–${endLabel}`,
        targetId: slice[0].id,
      });
    }
    return chunks;
  }, [episodes]);

  const seasonHeading =
    currentSeason !== undefined ? formatSeasonHeading(currentSeason) : detail.title;
  const badgeLabel =
    currentSeason?.number === 0
      ? "SPECIALS"
      : currentSeason?.number !== null && currentSeason?.number !== undefined
        ? `SEASON ${String(currentSeason.number).padStart(2, "0")}`
        : "SEASON";

  const jumpToEpisode = (epId: number) => {
    const el = document.getElementById(`season-ep-${epId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  return (
    <div className={classes.root}>
      {/* 顶部面包屑 + 切季栏 */}
      <div className={classes.topBar}>
        <nav className={classes.breadcrumb} aria-label="面包屑">
          <Link to={`/libraries/${detail.library_id}`}>
            {detail.library_name}
          </Link>
          <span className={classes.sep}>/</span>
          {detail.show !== null && (
            <>
              <Link to={`/items/${detail.show.id}`}>{detail.show.title}</Link>
              <span className={classes.sep}>/</span>
            </>
          )}
          <span className={classes.current}>{seasonHeading}</span>
        </nav>

        {sortedSeasons.length > 1 && (
          <div className={classes.switcher}>
            <button
              type="button"
              className={classes.switchBtn}
              disabled={prevSeason === undefined}
              onClick={() => {
                if (prevSeason !== undefined) {
                  void navigate(`/items/${prevSeason.id}`);
                }
              }}
            >
              <IconBack size={14} /> 上一季
            </button>
            <select
              className={classes.seasonSelect}
              aria-label="切换季"
              value={String(detail.id)}
              onChange={(e) => {
                void navigate(`/items/${e.target.value}`);
              }}
            >
              {sortedSeasons.map((s) => (
                <option key={s.id} value={String(s.id)}>
                  {formatSeasonHeading(s)} · {s.episode_count} 集
                </option>
              ))}
            </select>
            <button
              type="button"
              className={classes.switchBtn}
              disabled={nextSeason === undefined}
              onClick={() => {
                if (nextSeason !== undefined) {
                  void navigate(`/items/${nextSeason.id}`);
                }
              }}
            >
              下一季 <IconChevronRight size={14} />
            </button>
          </div>
        )}
      </div>

      {/* Linear 风格季 Hero 头图 */}
      <section className={classes.hero}>
        {detail.fanart_url !== null && (
          <div
            className={classes.heroBg}
            style={{ backgroundImage: `url("${detail.fanart_url}")` }}
          />
        )}
        <div className={classes.heroInner}>
          <div className={classes.poster}>
            {detail.poster_url !== null ? (
              <img src={detail.poster_url} alt={seasonHeading} />
            ) : (
              <div className={classes.posterBlank}>
                <span className={`${classes.posterBadge} mo-mono`}>
                  {badgeLabel}
                </span>
                <div>
                  <div className={classes.posterBlankTitle}>{seasonHeading}</div>
                  <div className={classes.posterBlankSub}>
                    {episodes.length} 集
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className={classes.heroContent}>
            {detail.show !== null && (
              <Link
                to={`/items/${detail.show.id}`}
                className={classes.showKicker}
              >
                <IconBack size={13} /> 返回《{detail.show.title}》
              </Link>
            )}

            <div className={classes.titleRow}>
              <h1 className={classes.title}>{seasonHeading}</h1>
              {releaseDate !== null && releaseDate !== undefined && (
                <span className={`${classes.airDate} mo-mono`}>
                  {releaseDate}
                </span>
              )}
            </div>

            <div className={classes.metaPills}>
              <Pill>
                <span className="mo-mono">{episodes.length} 集</span>
              </Pill>
            </div>

            {overviewText !== null &&
              overviewText !== undefined &&
              overviewText.trim() !== "" && (
                <p className={classes.overview}>{overviewText}</p>
              )}

            <div className={classes.actions}>
              {firstPlayable !== undefined && (
                <Button
                  leftSection={<IconPlay size={16} />}
                  loading={playingId === firstPlayable.id}
                  onClick={() => onPlayEpisode(firstPlayable)}
                >
                  播放第 {firstPlayable.number ?? "?"} 集
                </Button>
              )}
              {onPickPoster !== undefined && (
                <Button
                  variant="default"
                  leftSection={<IconImage size={16} />}
                  onClick={onPickPoster}
                >
                  换季海报
                </Button>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* 下方双栏：左侧干净的纵向分集列表 + 右侧资料栏 */}
      <div className={classes.bodyGrid}>
        <div className={classes.epColumn}>
          <div className={classes.sectionHead}>
            <div className={classes.sectionTitleGroup}>
              <h2>分集</h2>
              <small className="mo-mono">{episodes.length} 集</small>
            </div>

            {rangeChunks.length > 0 && (
              <div className={classes.rangeBar}>
                {rangeChunks.map((chunk) => (
                  <button
                    key={chunk.targetId}
                    type="button"
                    className={`${classes.rangeChip} mo-mono`}
                    onClick={() => jumpToEpisode(chunk.targetId)}
                  >
                    {chunk.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {episodes.length === 0 ? (
            <div className={classes.emptyList}>这一季还没有分集记录</div>
          ) : (
            <div className={classes.epList}>
              {episodes.map((ep) => {
                const epHref = `/items/${ep.id}`;
                const epLabel =
                  ep.number !== null ? `${ep.number}. ${ep.title}` : ep.title;

                return (
                  <div
                    key={ep.id}
                    id={`season-ep-${ep.id}`}
                    className={`${classes.epRow} ${
                      ep.has_file ? "" : classes.epMissing
                    }`}
                  >
                    <div className={classes.epThumb}>
                      <a
                        href={epHref}
                        className={classes.epThumbLink}
                        aria-label={epLabel}
                        onClick={(e) => {
                          e.preventDefault();
                          void navigate(epHref);
                        }}
                      >
                        {ep.still_url !== null ? (
                          <img
                            src={ep.still_url}
                            alt={ep.title}
                            className={classes.epThumbImg}
                            loading="lazy"
                          />
                        ) : (
                          <span className={`${classes.epThumbNum} mo-mono`}>
                            {ep.number !== null
                              ? String(ep.number).padStart(2, "0")
                              : "—"}
                          </span>
                        )}
                        {!ep.has_file && (
                          <span className={classes.missingBadge}>缺</span>
                        )}
                      </a>

                      {ep.playable && (
                        <button
                          type="button"
                          className={classes.thumbPlayOverlay}
                          aria-label={`播放第 ${ep.number ?? "?"} 集 ${ep.title}`}
                          disabled={playingId === ep.id}
                          onClick={() => onPlayEpisode(ep)}
                        >
                          <span className={classes.playCircle}>
                            <IconPlay size={16} />
                          </span>
                        </button>
                      )}
                    </div>

                    <div className={classes.epMain}>
                      <Link to={epHref} className={classes.epTitle}>
                        {epLabel}
                      </Link>
                      <div className={classes.epMeta}>
                        <span className="mo-mono">{ep.air_date ?? "—"}</span>
                        {ep.runtime !== null &&
                          ep.runtime !== undefined &&
                          ep.runtime > 0 && (
                            <>
                              <span>·</span>
                              <span>{ep.runtime} 分钟</span>
                            </>
                          )}
                      </div>
                      {ep.overview !== null &&
                        ep.overview !== undefined &&
                        ep.overview.trim() !== "" && (
                          <p className={classes.epOverview}>{ep.overview}</p>
                        )}
                    </div>

                    <div className={classes.epActions}>
                      {ep.playable && (
                        <button
                          type="button"
                          className={classes.iconBtn}
                          title="播放"
                          aria-label={`播放第 ${ep.number ?? "?"} 集`}
                          disabled={playingId === ep.id}
                          onClick={() => onPlayEpisode(ep)}
                        >
                          <IconPlay size={14} />
                        </button>
                      )}
                      <Link
                        to={epHref}
                        className={classes.iconBtn}
                        title="集详情"
                        aria-label={`查看第 ${ep.number ?? "?"} 集详情`}
                      >
                        <IconMore size={14} />
                      </Link>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className={classes.sideColumn}>
          {sidePanel}
          {imageSlots !== undefined && (
            <div className={classes.imageBox}>{imageSlots}</div>
          )}
        </div>
      </div>
    </div>
  );
}
