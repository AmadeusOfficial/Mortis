import { Checkbox, Group, NumberInput, Select, Switch, Textarea, TextInput } from "@mantine/core";
import { useId } from "react";
import type {
  MovieImages,
  MovieOutputConfig,
  MovieScrapeConfig,
  TvImages,
  TvOutputConfig,
  TvScrapeConfig,
} from "../../api";
import styles from "../../pages/SettingsPage.module.css";
import { Pill } from "../ui";
import { SettingRow } from "./SettingLayout";

const LANGUAGES = [
  { value: "zh-CN", label: "简体中文" },
  { value: "zh-TW", label: "繁体中文（台湾）" },
  { value: "zh-HK", label: "繁体中文（香港）" },
  { value: "en-US", label: "英语" },
  { value: "ja-JP", label: "日语" },
  { value: "ko-KR", label: "韩语" },
];
const POSTER_SIZES = [
  { value: "w342", label: "小（w342）" },
  { value: "w500", label: "中（w500）" },
  { value: "w780", label: "大（w780）" },
  { value: "original", label: "原图" },
];
const FANART_SIZES = [
  { value: "w780", label: "小（w780）" },
  { value: "w1280", label: "中（w1280）" },
  { value: "original", label: "大（原图）" },
];
const STILL_SIZES = [
  { value: "w185", label: "小（w185）" },
  { value: "w300", label: "中（w300）" },
  { value: "original", label: "大（原图）" },
];
type MovieImageSwitch = Exclude<keyof MovieImages, "extrafanart" | "extrafanart_count">;
const MOVIE_IMAGE_LABELS: [MovieImageSwitch, string][] = [
  ["poster", "海报"],
  ["fanart", "同人画"],
  ["clearlogo", "透明标志"],
  ["keyart", "关键艺术图"],
  ["banner", "横幅"],
  ["clearart", "透明艺术图"],
  ["discart", "光盘"],
  ["thumb", "缩略图"],
  ["collection_poster", "系列海报"],
  ["collection_fanart", "系列同人画"],
  ["actor_images", "演员头像"],
];
const TV_IMAGE_LABELS: [keyof TvImages, string][] = [
  ["poster", "海报"],
  ["fanart", "同人画"],
  ["clearlogo", "透明标志"],
  ["keyart", "关键艺术图"],
  ["banner", "横幅"],
  ["clearart", "透明艺术图"],
  ["thumb", "缩略图"],
  ["characterart", "角色图"],
  ["season_poster", "季海报"],
  ["season_banner", "季横幅"],
  ["season_thumb", "季缩略图"],
  ["episode_thumb", "集缩略图"],
  ["actor_images", "演员头像"],
];

// 宽高比检测还没做，先把开关占上，和 TMM 一样默认关。
function AspectRatioRow() {
  const id = useId();
  return (
    <SettingRow
      label={
        <Group gap={6} component="span">
          自动识别画面比例
          <Pill tone="neutral">未实现</Pill>
        </Group>
      }
      hint="刮削后检测视频实际的画面比例"
      htmlFor={id}
    >
      <Switch id={id} checked={false} disabled onChange={() => undefined} />
    </SettingRow>
  );
}

const TEMPLATE_HINT =
  "可用 ${title} ${originalTitle} ${year} ${edition} ${videoFormat} ${audioCodec} ${tmdb} ${imdb}；${前缀,标记,后缀} 只在标记有值时出现；给 Plex 用可在目录模板末尾加 ${{tmdb-,tmdb,}}";

const TV_TEMPLATE_HINT =
  "可用 ${showTitle} ${showOriginalTitle} ${showYear} ${seasonNr} ${seasonNr2} ${episodeNr} ${episodeNr2} ${title}（集标题）${tmdb} ${imdb}；${前缀,标记,后缀} 只在标记有值时出现";

export function countryInvalid(country: string): boolean {
  return !/^[A-Z]{2}$/.test(country);
}

// 阈值输入框里是文本，空、非数字或越界都算不合法。
export function thresholdInvalid(text: string): boolean {
  if (text.trim() === "") {
    return true;
  }
  const value = Number(text);
  return Number.isNaN(value) || value < 0 || value > 1;
}

export function parseSkipFolders(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

// 阈值按两位小数写，去掉多余的零；0.75 写成 "0.75"，0.5 写成 "0.5"。
export function formatThreshold(value: number): string {
  return String(Number(value.toFixed(2)));
}

// 识别和扫描：所有类型都有，保存时和语言、图片等一起整节提交。
export function ScanFields({
  thresholdText,
  onThresholdText,
  skipText,
  onSkipText,
  recursiveScan,
  onRecursiveScan,
}: {
  thresholdText: string;
  onThresholdText: (value: string) => void;
  skipText: string;
  onSkipText: (value: string) => void;
  recursiveScan?: boolean;
  onRecursiveScan?: (value: boolean) => void;
}) {
  const thresholdId = useId();
  const thresholdHintId = useId();
  const skipId = useId();
  const skipHintId = useId();
  const recursiveId = useId();
  const recursiveHintId = useId();

  return (
    <div className={styles.fieldList}>
      <SettingRow
        label="匹配阈值"
        hint="0 到 1 之间，越接近 1 越严格"
        htmlFor={thresholdId}
        hintId={thresholdHintId}
      >
        <TextInput
          id={thresholdId}
          aria-describedby={thresholdHintId}
          className={styles.monoInput}
          maw={140}
          inputMode="decimal"
          value={thresholdText}
          onChange={(event) => onThresholdText(event.currentTarget.value)}
          error={
            thresholdInvalid(thresholdText) ? "填 0 到 1 之间的数，例如 0.75" : undefined
          }
        />
      </SettingRow>
      <SettingRow
        label="跳过的目录"
        hint="一行一个目录名，扫描时整个目录不看"
        htmlFor={skipId}
        hintId={skipHintId}
        align="start"
      >
        <Textarea
          id={skipId}
          aria-describedby={skipHintId}
          className={styles.monoInput}
          autosize
          minRows={2}
          value={skipText}
          onChange={(event) => onSkipText(event.currentTarget.value)}
        />
      </SettingRow>
      {onRecursiveScan !== undefined && (
        <SettingRow
          label="递归搜索子目录"
          hint="打开后继续往下找游戏。文件夹里要同时有 exe，以及 xp3、arc、dat、ini、dll、txt、pac、noa、sh、bin、pck 中的一种，才算一款游戏；系列文件夹本身不会被收进来。关掉则只把游戏库下的一级文件夹当作游戏。"
          htmlFor={recursiveId}
          hintId={recursiveHintId}
        >
          <Switch
            id={recursiveId}
            aria-describedby={recursiveHintId}
            checked={recursiveScan === true}
            onChange={(event) => onRecursiveScan(event.currentTarget.checked)}
          />
        </SettingRow>
      )}
    </div>
  );
}

export function MovieScrapeFields({
  movie,
  onChange,
}: {
  movie: MovieScrapeConfig;
  onChange: (patch: Partial<MovieScrapeConfig>) => void;
}) {
  const langId = useId();
  const countryId = useId();
  const countryHintId = useId();
  const adultId = useId();
  const posterId = useId();
  const fanartId = useId();
  const textlessId = useId();
  const extraId = useId();
  const extraHintId = useId();

  return (
    <div className={styles.fieldList}>
      <SettingRow label="刮削语言" hint="片名、简介、标语优先使用的语言" htmlFor={langId}>
        <Select
          id={langId}
          maw={260}
          data={LANGUAGES}
          allowDeselect={false}
          value={movie.language}
          onChange={(value) => value !== null && onChange({ language: value })}
        />
      </SettingRow>
      <SettingRow
        label="分级国家"
        hint="TMDB 的国家代码，例如 US、CN"
        htmlFor={countryId}
        hintId={countryHintId}
      >
        <TextInput
          id={countryId}
          aria-describedby={countryHintId}
          className={styles.monoInput}
          maw={140}
          maxLength={2}
          value={movie.certification_country}
          error={countryInvalid(movie.certification_country) ? "填两位字母" : undefined}
          onChange={(event) =>
            onChange({ certification_country: event.currentTarget.value.toUpperCase() })
          }
        />
      </SettingRow>
      <SettingRow label="包含成人内容" hint="搜索与匹配结果是否包含成人条目" htmlFor={adultId}>
        <Switch
          id={adultId}
          checked={movie.include_adult}
          onChange={(event) => onChange({ include_adult: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="海报尺寸" htmlFor={posterId}>
        <Select
          id={posterId}
          maw={220}
          data={POSTER_SIZES}
          allowDeselect={false}
          value={movie.poster_size}
          onChange={(value) => value !== null && onChange({ poster_size: value })}
        />
      </SettingRow>
      <SettingRow label="同人画尺寸" htmlFor={fanartId}>
        <Select
          id={fanartId}
          maw={220}
          data={FANART_SIZES}
          allowDeselect={false}
          value={movie.fanart_size}
          onChange={(value) => value !== null && onChange({ fanart_size: value })}
        />
      </SettingRow>
      <SettingRow label="优先无文字的同人画" htmlFor={textlessId}>
        <Switch
          id={textlessId}
          checked={movie.prefer_textless_fanart}
          onChange={(event) => onChange({ prefer_textless_fanart: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="要抓的图片" hint="刮削时一并下载的图片资产" align="start">
        <Group gap="md" wrap="wrap">
          {MOVIE_IMAGE_LABELS.map(([key, label]) => (
            <Checkbox
              key={key}
              label={label}
              checked={movie.images[key]}
              onChange={(event) =>
                onChange({ images: { ...movie.images, [key]: event.currentTarget.checked } })
              }
            />
          ))}
        </Group>
      </SettingRow>
      <SettingRow
        label={
          <Group gap={6} component="span">
            额外同人画
            <Pill tone="neutral">未实现</Pill>
          </Group>
        }
        hint="除主同人画外再抓几张，写进 extrafanart 目录；下载和写出还没做"
        htmlFor={extraId}
        hintId={extraHintId}
      >
        <Group gap="sm" wrap="nowrap">
          <Switch
            id={extraId}
            aria-describedby={extraHintId}
            checked={movie.images.extrafanart}
            onChange={(event) =>
              onChange({ images: { ...movie.images, extrafanart: event.currentTarget.checked } })
            }
          />
          <NumberInput
            aria-label="额外同人画数量"
            className={styles.monoInput}
            w={110}
            min={1}
            max={20}
            allowDecimal={false}
            suffix=" 张"
            disabled={!movie.images.extrafanart}
            value={movie.images.extrafanart_count}
            onChange={(value) => {
              if (typeof value === "number" && value >= 1 && value <= 20) {
                onChange({ images: { ...movie.images, extrafanart_count: value } });
              }
            }}
          />
        </Group>
      </SettingRow>
      <AspectRatioRow />
    </div>
  );
}

export function MovieOrganizeFields({
  output,
  onChange,
}: {
  output: MovieOutputConfig;
  onChange: (patch: Partial<MovieOutputConfig>) => void;
}) {
  const autoId = useId();
  const renameId = useId();
  const renameHintId = useId();
  const writeId = useId();
  const writeHintId = useId();
  const imageStyleId = useId();
  const collectionId = useId();
  const actorFolderId = useId();
  const actorFolderHintId = useId();
  const folderTplId = useId();
  const folderTplHintId = useId();
  const fileTplId = useId();
  const fileTplHintId = useId();
  const folderSpaceId = useId();
  const fileSpaceId = useId();
  const colonId = useId();
  const lockId = useId();
  const studioId = useId();

  return (
    <div className={styles.fieldList}>
      <SettingRow label="刮削后自动整理" htmlFor={autoId}>
        <Switch
          id={autoId}
          checked={output.organize_after_scrape}
          onChange={(event) => onChange({ organize_after_scrape: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow
        label="整理时重命名"
        hint="按下面的模板改目录和文件名"
        htmlFor={renameId}
        hintId={renameHintId}
      >
        <Switch
          id={renameId}
          aria-describedby={renameHintId}
          checked={output.organize_rename}
          onChange={(event) => onChange({ organize_rename: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow
        label="整理时写 NFO 和图片"
        hint="写到视频旁边；已经写过的条目改名后总会重写"
        htmlFor={writeId}
        hintId={writeHintId}
      >
        <Switch
          id={writeId}
          aria-describedby={writeHintId}
          checked={output.organize_write_files}
          onChange={(event) => onChange({ organize_write_files: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="NFO 文件名">
        <Checkbox.Group
          aria-label="NFO 文件名"
          value={output.nfo_names}
          onChange={(value) => onChange({ nfo_names: value as ("movie" | "filename")[] })}
        >
          <Group gap="md">
            <Checkbox value="movie" label="movie.nfo" />
            <Checkbox value="filename" label="视频名.nfo" />
          </Group>
        </Checkbox.Group>
      </SettingRow>
      <SettingRow label="图片文件名" htmlFor={imageStyleId}>
        <Select
          id={imageStyleId}
          maw={260}
          data={[
            { value: "filename", label: "视频名-poster.jpg" },
            { value: "plain", label: "poster.jpg" },
          ]}
          allowDeselect={false}
          value={output.image_style}
          onChange={(value) => value !== null && onChange({ image_style: value as "filename" | "plain" })}
        />
      </SettingRow>
      <SettingRow label="系列图写成 movieset-poster.jpg" htmlFor={collectionId}>
        <Switch
          id={collectionId}
          checked={output.collection_image_names}
          onChange={(event) => onChange({ collection_image_names: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow
        label="演员头像目录"
        hint="只填一层目录名，放在视频旁边"
        htmlFor={actorFolderId}
        hintId={actorFolderHintId}
      >
        <TextInput
          id={actorFolderId}
          aria-describedby={actorFolderHintId}
          className={styles.monoInput}
          maw={260}
          value={output.actor_images_folder}
          onChange={(event) => onChange({ actor_images_folder: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow
        label="目录模板"
        hint={TEMPLATE_HINT}
        htmlFor={folderTplId}
        hintId={folderTplHintId}
        align="start"
      >
        <TextInput
          id={folderTplId}
          aria-describedby={folderTplHintId}
          className={styles.monoInput}
          value={output.folder_template}
          onChange={(event) => onChange({ folder_template: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow
        label="文件模板"
        hint={TEMPLATE_HINT}
        htmlFor={fileTplId}
        hintId={fileTplHintId}
        align="start"
      >
        <TextInput
          id={fileTplId}
          aria-describedby={fileTplHintId}
          className={styles.monoInput}
          value={output.file_template}
          onChange={(event) => onChange({ file_template: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="目录名空格换成" htmlFor={folderSpaceId}>
        <TextInput
          id={folderSpaceId}
          className={styles.monoInput}
          maw={120}
          placeholder="不换"
          maxLength={3}
          value={output.folder_space}
          onChange={(event) => onChange({ folder_space: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="文件名空格换成" htmlFor={fileSpaceId}>
        <TextInput
          id={fileSpaceId}
          className={styles.monoInput}
          maw={120}
          maxLength={3}
          value={output.file_space}
          onChange={(event) => onChange({ file_space: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="冒号换成" htmlFor={colonId}>
        <TextInput
          id={colonId}
          className={styles.monoInput}
          maw={120}
          maxLength={3}
          value={output.colon_replacement}
          onChange={(event) => onChange({ colon_replacement: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="NFO 写入锁定标记" htmlFor={lockId}>
        <Switch
          id={lockId}
          checked={output.nfo_write_lockdata}
          onChange={(event) => onChange({ nfo_write_lockdata: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="NFO 只写一个制片公司" htmlFor={studioId}>
        <Switch
          id={studioId}
          checked={output.nfo_single_studio}
          onChange={(event) => onChange({ nfo_single_studio: event.currentTarget.checked })}
        />
      </SettingRow>
    </div>
  );
}

export function SeriesScrapeFields({
  series,
  onChange,
}: {
  series: TvScrapeConfig;
  onChange: (patch: Partial<TvScrapeConfig>) => void;
}) {
  const langId = useId();
  const countryId = useId();
  const countryHintId = useId();
  const adultId = useId();
  const posterId = useId();
  const fanartId = useId();
  const stillId = useId();
  const textlessId = useId();
  const missingEpId = useId();
  const missingEpHintId = useId();
  const missingSpecId = useId();

  return (
    <div className={styles.fieldList}>
      <SettingRow label="刮削语言" hint="剧名、简介、标语优先使用的语言" htmlFor={langId}>
        <Select
          id={langId}
          maw={260}
          data={LANGUAGES}
          allowDeselect={false}
          value={series.language}
          onChange={(value) => value !== null && onChange({ language: value })}
        />
      </SettingRow>
      <SettingRow
        label="分级国家"
        hint="TMDB 的国家代码，例如 US、CN"
        htmlFor={countryId}
        hintId={countryHintId}
      >
        <TextInput
          id={countryId}
          aria-describedby={countryHintId}
          className={styles.monoInput}
          maw={140}
          maxLength={2}
          value={series.certification_country}
          error={countryInvalid(series.certification_country) ? "填两位字母" : undefined}
          onChange={(event) =>
            onChange({ certification_country: event.currentTarget.value.toUpperCase() })
          }
        />
      </SettingRow>
      <SettingRow label="包含成人内容" hint="搜索与匹配结果是否包含成人条目" htmlFor={adultId}>
        <Switch
          id={adultId}
          checked={series.include_adult}
          onChange={(event) => onChange({ include_adult: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="海报尺寸" htmlFor={posterId}>
        <Select
          id={posterId}
          maw={220}
          data={POSTER_SIZES}
          allowDeselect={false}
          value={series.poster_size}
          onChange={(value) => value !== null && onChange({ poster_size: value })}
        />
      </SettingRow>
      <SettingRow label="同人画尺寸" htmlFor={fanartId}>
        <Select
          id={fanartId}
          maw={220}
          data={FANART_SIZES}
          allowDeselect={false}
          value={series.fanart_size}
          onChange={(value) => value !== null && onChange({ fanart_size: value })}
        />
      </SettingRow>
      <SettingRow label="集缩略图尺寸" htmlFor={stillId}>
        <Select
          id={stillId}
          maw={220}
          data={STILL_SIZES}
          allowDeselect={false}
          value={series.still_size}
          onChange={(value) => value !== null && onChange({ still_size: value })}
        />
      </SettingRow>
      <SettingRow label="优先无文字的同人画" htmlFor={textlessId}>
        <Switch
          id={textlessId}
          checked={series.prefer_textless_fanart}
          onChange={(event) => onChange({ prefer_textless_fanart: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="要抓的图片" hint="刮削时一并下载的图片资产" align="start">
        <Group gap="md" wrap="wrap">
          {TV_IMAGE_LABELS.map(([key, label]) => (
            <Checkbox
              key={key}
              label={label}
              checked={series.images[key]}
              onChange={(event) =>
                onChange({ images: { ...series.images, [key]: event.currentTarget.checked } })
              }
            />
          ))}
        </Group>
      </SettingRow>
      <AspectRatioRow />
      <SettingRow
        label="显示缺少的集"
        hint="已播出但本地没有文件的集也列出来，灰色标「缺」"
        htmlFor={missingEpId}
        hintId={missingEpHintId}
      >
        <Switch
          id={missingEpId}
          aria-describedby={missingEpHintId}
          checked={series.show_missing_episodes}
          onChange={(event) => onChange({ show_missing_episodes: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="显示缺少的特别篇" htmlFor={missingSpecId}>
        <Switch
          id={missingSpecId}
          checked={series.show_missing_specials}
          onChange={(event) => onChange({ show_missing_specials: event.currentTarget.checked })}
        />
      </SettingRow>
    </div>
  );
}

export function SeriesOrganizeFields({
  output,
  onChange,
}: {
  output: TvOutputConfig;
  onChange: (patch: Partial<TvOutputConfig>) => void;
}) {
  const autoId = useId();
  const renameId = useId();
  const renameHintId = useId();
  const writeId = useId();
  const writeHintId = useId();
  const showTplId = useId();
  const showTplHintId = useId();
  const seasonTplId = useId();
  const fileTplId = useId();
  const fileTplHintId = useId();
  const showSpaceId = useId();
  const seasonSpaceId = useId();
  const fileSpaceId = useId();
  const colonId = useId();
  const colonHintId = useId();
  const lockId = useId();
  const studioId = useId();

  return (
    <div className={styles.fieldList}>
      <SettingRow label="刮削后自动整理" htmlFor={autoId}>
        <Switch
          id={autoId}
          checked={output.organize_after_scrape}
          onChange={(event) => onChange({ organize_after_scrape: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow
        label="整理时重命名"
        hint="按下面的模板改剧目录、季目录和文件名"
        htmlFor={renameId}
        hintId={renameHintId}
      >
        <Switch
          id={renameId}
          aria-describedby={renameHintId}
          checked={output.organize_rename}
          onChange={(event) => onChange({ organize_rename: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow
        label="整理时写 NFO 和图片"
        hint="tvshow.nfo 和剧图写进剧目录，season.nfo 写进季目录，集 NFO 和缩略图写在视频旁边"
        htmlFor={writeId}
        hintId={writeHintId}
      >
        <Switch
          id={writeId}
          aria-describedby={writeHintId}
          checked={output.organize_write_files}
          onChange={(event) => onChange({ organize_write_files: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow
        label="剧目录模板"
        hint={TV_TEMPLATE_HINT}
        htmlFor={showTplId}
        hintId={showTplHintId}
        align="start"
      >
        <TextInput
          id={showTplId}
          aria-describedby={showTplHintId}
          className={styles.monoInput}
          value={output.show_template}
          onChange={(event) => onChange({ show_template: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="季目录模板" htmlFor={seasonTplId}>
        <TextInput
          id={seasonTplId}
          className={styles.monoInput}
          value={output.season_template}
          onChange={(event) => onChange({ season_template: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow
        label="文件模板"
        hint="一个文件里有多集时，${episodeNr2} 写成 01E02"
        htmlFor={fileTplId}
        hintId={fileTplHintId}
        align="start"
      >
        <TextInput
          id={fileTplId}
          aria-describedby={fileTplHintId}
          className={styles.monoInput}
          value={output.file_template}
          onChange={(event) => onChange({ file_template: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="剧目录名空格换成" htmlFor={showSpaceId}>
        <TextInput
          id={showSpaceId}
          className={styles.monoInput}
          maw={120}
          placeholder="不换"
          maxLength={3}
          value={output.show_space}
          onChange={(event) => onChange({ show_space: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="季目录名空格换成" htmlFor={seasonSpaceId}>
        <TextInput
          id={seasonSpaceId}
          className={styles.monoInput}
          maw={120}
          placeholder="不换"
          maxLength={3}
          value={output.season_space}
          onChange={(event) => onChange({ season_space: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="文件名空格换成" htmlFor={fileSpaceId}>
        <TextInput
          id={fileSpaceId}
          className={styles.monoInput}
          maw={120}
          maxLength={3}
          value={output.file_space}
          onChange={(event) => onChange({ file_space: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow
        label="冒号换成"
        hint="只换半角冒号，全角「：」保留"
        htmlFor={colonId}
        hintId={colonHintId}
      >
        <TextInput
          id={colonId}
          aria-describedby={colonHintId}
          className={styles.monoInput}
          maw={120}
          maxLength={3}
          value={output.colon_replacement}
          onChange={(event) => onChange({ colon_replacement: event.currentTarget.value })}
        />
      </SettingRow>
      <SettingRow label="NFO 写入锁定标记" htmlFor={lockId}>
        <Switch
          id={lockId}
          checked={output.nfo_write_lockdata}
          onChange={(event) => onChange({ nfo_write_lockdata: event.currentTarget.checked })}
        />
      </SettingRow>
      <SettingRow label="NFO 只写一个制片公司" htmlFor={studioId}>
        <Switch
          id={studioId}
          checked={output.nfo_single_studio}
          onChange={(event) => onChange({ nfo_single_studio: event.currentTarget.checked })}
        />
      </SettingRow>
    </div>
  );
}
