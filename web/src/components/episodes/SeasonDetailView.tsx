import { Button, Menu } from "@mantine/core";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import type { EpisodeInfo, ItemDetail } from "../../api";
import {
  IconBack,
  IconChevronDown,
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

const PAGE_SIZE = 30;

function buildPageTokens(current: number, total: number): (number | "dots-l" | "dots-r")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const tokens: (number | "dots-l" | "dots-r")[] = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);
  if (left > 2) {
    tokens.push("dots-l");
  }
  for (let p = left; p <= right; p++) {
    tokens.push(p);
  }
  if (right < total - 1) {
    tokens.push("dots-r");
  }
  tokens.push(total);
  return tokens;
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
  const [page, setPage] = useState(1);

  useEffect(() => {
    setPage(1);
  }, [detail.id]);

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

  const totalPages = Math.max(1, Math.ceil(episodes.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedEpisodes = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return episodes.slice(start, start + PAGE_SIZE);
  }, [episodes, safePage]);

  const pageTokens = useMemo(
    () => buildPageTokens(safePage, totalPages),
    [safePage, totalPages],
  );

  const pageRanges = useMemo(() => {
    if (totalPages <= 1) {
      return [];
    }
    return Array.from({ length: totalPages }, (_, idx) => {
      const startIdx = idx * PAGE_SIZE;
      const endIdx = Math.min(episodes.length - 1, startIdx + PAGE_SIZE - 1);
      const startNum = episodes[startIdx]?.number ?? startIdx + 1;
      const endNum = episodes[endIdx]?.number ?? endIdx + 1;
      return {
        page: idx + 1,
        label: `第 ${startNum}–${endNum} 集`,
      };
    });
  }, [episodes, totalPages]);

  const seasonHeading =
    currentSeason !== undefined ? formatSeasonHeading(currentSeason) : detail.title;
  const badgeLabel =
    currentSeason?.number === 0
      ? "SPECIALS"
      : currentSeason?.number !== null && currentSeason?.number !== undefined
        ? `SEASON ${String(currentSeason.number).padStart(2, "0")}`
        : "SEASON";

  const goToPage = (nextPage: number, scrollTop = false) => {
    const clamped = Math.max(1, Math.min(totalPages, nextPage));
    setPage(clamped);
    if (scrollTop) {
      document
        .getElementById("season-episodes-top")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  const renderPager = (scrollTop: boolean) => {
    if (totalPages <= 1) {
      return null;
    }
    const currentRange = pageRanges.find((r) => r.page === safePage);
    return (
      <div className={classes.pager}>
        <Menu position="bottom-end" withinPortal>
          <Menu.Target>
            <button
              type="button"
              className={`${classes.dropdownTrigger} mo-mono`}
              aria-label="选择分集页"
            >
              <span>{currentRange?.label ?? `第 ${safePage} 页`}</span>
              <IconChevronDown size={13} />
            </button>
          </Menu.Target>
          <Menu.Dropdown className={classes.menuDropdown}>
            <div className={classes.menuScroll}>
              {pageRanges.map((r) => (
                <Menu.Item
                  key={r.page}
                  className={`${classes.menuItem} ${
                    r.page === safePage ? classes.menuItemActive : ""
                  } mo-mono`}
                  onClick={() => goToPage(r.page, scrollTop)}
                >
                  {r.label}
                </Menu.Item>
              ))}
            </div>
          </Menu.Dropdown>
        </Menu>

        <div className={classes.pageGroup}>
          <button
            type="button"
            className={classes.pageBtn}
            aria-label="上一页"
            disabled={safePage <= 1}
            onClick={() => goToPage(safePage - 1, scrollTop)}
          >
            <IconBack size={13} />
          </button>
          {pageTokens.map((token) =>
            typeof token === "number" ? (
              <button
                key={token}
                type="button"
                className={`${classes.pageBtn} ${
                  token === safePage ? classes.pageBtnActive : ""
                } mo-mono`}
                onClick={() => goToPage(token, scrollTop)}
              >
                {token}
              </button>
            ) : (
              <span key={token} className={`${classes.pageDots} mo-mono`}>
                …
              </span>
            ),
          )}
          <button
            type="button"
            className={classes.pageBtn}
            aria-label="下一页"
            disabled={safePage >= totalPages}
            onClick={() => goToPage(safePage + 1, scrollTop)}
          >
            <IconChevronRight size={13} />
          </button>
        </div>
      </div>
    );
  };

  const backdropUrl =
    detail.fanart_url ?? detail.poster_url ?? detail.show?.poster_url ?? null;

  return (
    <div className={classes.root}>
      {backdropUrl !== null && (
        <div className={classes.backdropWrap} aria-hidden="true">
          <div
            className={classes.backdropImg}
            style={{ backgroundImage: `url("${backdropUrl}")` }}
          />
          <div className={classes.backdropScrim} />
        </div>
      )}

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
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <button
                  type="button"
                  className={classes.seasonTrigger}
                  aria-label="切换季"
                >
                  <span>
                    {seasonHeading}
                    {currentSeason !== undefined
                      ? ` · ${currentSeason.episode_count} 集`
                      : ""}
                  </span>
                  <IconChevronDown size={13} />
                </button>
              </Menu.Target>
              <Menu.Dropdown className={classes.menuDropdown}>
                <div className={classes.menuScroll}>
                  {sortedSeasons.map((s) => (
                    <Menu.Item
                      key={s.id}
                      className={`${classes.menuItem} ${
                        s.id === detail.id ? classes.menuItemActive : ""
                      }`}
                      onClick={() => void navigate(`/items/${s.id}`)}
                    >
                      {formatSeasonHeading(s)} · {s.episode_count} 集
                    </Menu.Item>
                  ))}
                </div>
              </Menu.Dropdown>
            </Menu>
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

      {/* 通透无框季 Hero 区 */}
      <section className={classes.hero}>
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
        <div className={classes.epColumn} id="season-episodes-top">
          <div className={classes.sectionHead}>
            <div className={classes.sectionTitleGroup}>
              <h2>分集</h2>
              <small className="mo-mono">{episodes.length} 集</small>
            </div>

            {renderPager(false)}
          </div>

          {episodes.length === 0 ? (
            <div className={classes.emptyList}>这一季还没有分集记录</div>
          ) : (
            <>
              <div className={classes.epList}>
                {pagedEpisodes.map((ep) => {
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
              {totalPages > 1 && (
                <div className={classes.bottomPager}>{renderPager(true)}</div>
              )}
            </>
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
