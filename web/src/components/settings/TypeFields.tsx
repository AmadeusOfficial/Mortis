import {
  Checkbox,
  Group,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import type {
  MovieImages,
  MovieOutputConfig,
  MovieScrapeConfig,
  TvImages,
  TvOutputConfig,
  TvScrapeConfig,
} from "../../api";

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
const MOVIE_IMAGE_LABELS: [keyof MovieImages, string][] = [
  ["poster", "海报"],
  ["fanart", "同人画"],
  ["clearlogo", "透明标志"],
  ["keyart", "关键艺术图"],
  ["collection_poster", "系列海报"],
  ["collection_fanart", "系列同人画"],
  ["actor_images", "演员头像"],
];
const TV_IMAGE_LABELS: [keyof TvImages, string][] = [
  ["poster", "海报"],
  ["fanart", "同人画"],
  ["clearlogo", "透明标志"],
  ["keyart", "关键艺术图"],
  ["season_poster", "季海报"],
  ["episode_thumb", "集缩略图"],
  ["actor_images", "演员头像"],
];

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
}: {
  thresholdText: string;
  onThresholdText: (value: string) => void;
  skipText: string;
  onSkipText: (value: string) => void;
}) {
  return (
    <Stack gap="md">
      <Text size="xs" c="dimmed">
        目录名和来源结果的相似度达到阈值才自动认定；跳过的目录在扫描时整个不看。
      </Text>
      <TextInput
        label="匹配阈值"
        description="0 到 1 之间，越接近 1 越严格"
        inputMode="decimal"
        value={thresholdText}
        onChange={(event) => onThresholdText(event.currentTarget.value)}
        error={
          thresholdInvalid(thresholdText) ? "填 0 到 1 之间的数，例如 0.75" : undefined
        }
      />
      <Textarea
        label="跳过的目录"
        description="一行一个目录名，扫描时整个目录不看"
        autosize
        minRows={2}
        value={skipText}
        onChange={(event) => onSkipText(event.currentTarget.value)}
      />
    </Stack>
  );
}

export function MovieScrapeFields({
  movie,
  onChange,
}: {
  movie: MovieScrapeConfig;
  onChange: (patch: Partial<MovieScrapeConfig>) => void;
}) {
  return (
    <Stack gap="md">
      <Group grow align="flex-start">
        <Select
          label="刮削语言"
          data={LANGUAGES}
          allowDeselect={false}
          value={movie.language}
          onChange={(value) => value !== null && onChange({ language: value })}
        />
        <TextInput
          label="分级国家"
          description="TMDB 的国家代码，例如 US、CN"
          maxLength={2}
          value={movie.certification_country}
          error={countryInvalid(movie.certification_country) ? "填两位字母" : undefined}
          onChange={(event) =>
            onChange({ certification_country: event.currentTarget.value.toUpperCase() })
          }
        />
      </Group>
      <Switch
        label="包含成人内容"
        checked={movie.include_adult}
        onChange={(event) => onChange({ include_adult: event.currentTarget.checked })}
      />
      <Group grow align="flex-start">
        <Select
          label="海报尺寸"
          data={POSTER_SIZES}
          allowDeselect={false}
          value={movie.poster_size}
          onChange={(value) => value !== null && onChange({ poster_size: value })}
        />
        <Select
          label="同人画尺寸"
          data={FANART_SIZES}
          allowDeselect={false}
          value={movie.fanart_size}
          onChange={(value) => value !== null && onChange({ fanart_size: value })}
        />
      </Group>
      <Switch
        label="优先无文字的同人画"
        checked={movie.prefer_textless_fanart}
        onChange={(event) => onChange({ prefer_textless_fanart: event.currentTarget.checked })}
      />
      <Stack gap={6}>
        <Text size="sm" fw={500}>
          要抓的图片
        </Text>
        <Group gap="md">
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
      </Stack>
    </Stack>
  );
}

export function MovieOrganizeFields({
  output,
  onChange,
}: {
  output: MovieOutputConfig;
  onChange: (patch: Partial<MovieOutputConfig>) => void;
}) {
  return (
    <Stack gap="md">
      <Text size="xs" c="dimmed">
        刮削只改莫提斯里的资料；整理才动磁盘，让 Emby、Plex、Kodi 能直接读到。
      </Text>
      <Switch
        label="刮削后自动整理"
        checked={output.organize_after_scrape}
        onChange={(event) => onChange({ organize_after_scrape: event.currentTarget.checked })}
      />
      <Switch
        label="整理时重命名"
        description="按下面的模板改目录和文件名"
        checked={output.organize_rename}
        onChange={(event) => onChange({ organize_rename: event.currentTarget.checked })}
      />
      <Switch
        label="整理时写 NFO 和图片"
        description="写到视频旁边；已经写过的条目改名后总会重写"
        checked={output.organize_write_files}
        onChange={(event) => onChange({ organize_write_files: event.currentTarget.checked })}
      />
      <Checkbox.Group
        label="NFO 文件名"
        value={output.nfo_names}
        onChange={(value) => onChange({ nfo_names: value as ("movie" | "filename")[] })}
      >
        <Group gap="md" mt={4}>
          <Checkbox value="movie" label="movie.nfo" />
          <Checkbox value="filename" label="视频名.nfo" />
        </Group>
      </Checkbox.Group>
      <Select
        label="图片文件名"
        data={[
          { value: "filename", label: "视频名-poster.jpg" },
          { value: "plain", label: "poster.jpg" },
        ]}
        allowDeselect={false}
        value={output.image_style}
        onChange={(value) => value !== null && onChange({ image_style: value as "filename" | "plain" })}
      />
      <Switch
        label="系列图写成 movieset-poster.jpg"
        checked={output.collection_image_names}
        onChange={(event) => onChange({ collection_image_names: event.currentTarget.checked })}
      />
      <TextInput
        label="演员头像目录"
        description="只填一层目录名，放在视频旁边"
        value={output.actor_images_folder}
        onChange={(event) => onChange({ actor_images_folder: event.currentTarget.value })}
      />
      <TextInput
        label="目录模板"
        description={TEMPLATE_HINT}
        value={output.folder_template}
        onChange={(event) => onChange({ folder_template: event.currentTarget.value })}
      />
      <TextInput
        label="文件模板"
        description={TEMPLATE_HINT}
        value={output.file_template}
        onChange={(event) => onChange({ file_template: event.currentTarget.value })}
      />
      <Group grow align="flex-start">
        <TextInput
          label="目录名空格换成"
          placeholder="不换"
          maxLength={3}
          value={output.folder_space}
          onChange={(event) => onChange({ folder_space: event.currentTarget.value })}
        />
        <TextInput
          label="文件名空格换成"
          maxLength={3}
          value={output.file_space}
          onChange={(event) => onChange({ file_space: event.currentTarget.value })}
        />
        <TextInput
          label="冒号换成"
          maxLength={3}
          value={output.colon_replacement}
          onChange={(event) => onChange({ colon_replacement: event.currentTarget.value })}
        />
      </Group>
      <Switch
        label="NFO 写入锁定标记"
        checked={output.nfo_write_lockdata}
        onChange={(event) => onChange({ nfo_write_lockdata: event.currentTarget.checked })}
      />
      <Switch
        label="NFO 只写一个制片公司"
        checked={output.nfo_single_studio}
        onChange={(event) => onChange({ nfo_single_studio: event.currentTarget.checked })}
      />
    </Stack>
  );
}

export function SeriesScrapeFields({
  series,
  onChange,
}: {
  series: TvScrapeConfig;
  onChange: (patch: Partial<TvScrapeConfig>) => void;
}) {
  return (
    <Stack gap="md">
      <Group grow align="flex-start">
        <Select
          label="刮削语言"
          data={LANGUAGES}
          allowDeselect={false}
          value={series.language}
          onChange={(value) => value !== null && onChange({ language: value })}
        />
        <TextInput
          label="分级国家"
          description="TMDB 的国家代码，例如 US、CN"
          maxLength={2}
          value={series.certification_country}
          error={countryInvalid(series.certification_country) ? "填两位字母" : undefined}
          onChange={(event) =>
            onChange({ certification_country: event.currentTarget.value.toUpperCase() })
          }
        />
      </Group>
      <Switch
        label="包含成人内容"
        checked={series.include_adult}
        onChange={(event) => onChange({ include_adult: event.currentTarget.checked })}
      />
      <Group grow align="flex-start">
        <Select
          label="海报尺寸"
          data={POSTER_SIZES}
          allowDeselect={false}
          value={series.poster_size}
          onChange={(value) => value !== null && onChange({ poster_size: value })}
        />
        <Select
          label="同人画尺寸"
          data={FANART_SIZES}
          allowDeselect={false}
          value={series.fanart_size}
          onChange={(value) => value !== null && onChange({ fanart_size: value })}
        />
        <Select
          label="集缩略图尺寸"
          data={STILL_SIZES}
          allowDeselect={false}
          value={series.still_size}
          onChange={(value) => value !== null && onChange({ still_size: value })}
        />
      </Group>
      <Switch
        label="优先无文字的同人画"
        checked={series.prefer_textless_fanart}
        onChange={(event) => onChange({ prefer_textless_fanart: event.currentTarget.checked })}
      />
      <Stack gap={6}>
        <Text size="sm" fw={500}>
          要抓的图片
        </Text>
        <Group gap="md">
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
      </Stack>
      <Switch
        label="显示缺少的集"
        description="已播出但本地没有文件的集也列出来，灰色标「缺」"
        checked={series.show_missing_episodes}
        onChange={(event) => onChange({ show_missing_episodes: event.currentTarget.checked })}
      />
      <Switch
        label="显示缺少的特别篇"
        checked={series.show_missing_specials}
        onChange={(event) => onChange({ show_missing_specials: event.currentTarget.checked })}
      />
    </Stack>
  );
}

export function SeriesOrganizeFields({
  output,
  onChange,
}: {
  output: TvOutputConfig;
  onChange: (patch: Partial<TvOutputConfig>) => void;
}) {
  return (
    <Stack gap="md">
      <Text size="xs" c="dimmed">
        刮削只改莫提斯里的资料；整理才动磁盘，让 Emby、Plex、Kodi 能直接读到。
      </Text>
      <Switch
        label="刮削后自动整理"
        checked={output.organize_after_scrape}
        onChange={(event) => onChange({ organize_after_scrape: event.currentTarget.checked })}
      />
      <Switch
        label="整理时重命名"
        description="按下面的模板改剧目录、季目录和文件名"
        checked={output.organize_rename}
        onChange={(event) => onChange({ organize_rename: event.currentTarget.checked })}
      />
      <Switch
        label="整理时写 NFO 和图片"
        description="tvshow.nfo 和剧图写进剧目录，season.nfo 写进季目录，集 NFO 和缩略图写在视频旁边"
        checked={output.organize_write_files}
        onChange={(event) => onChange({ organize_write_files: event.currentTarget.checked })}
      />
      <TextInput
        label="剧目录模板"
        description={TV_TEMPLATE_HINT}
        value={output.show_template}
        onChange={(event) => onChange({ show_template: event.currentTarget.value })}
      />
      <TextInput
        label="季目录模板"
        value={output.season_template}
        onChange={(event) => onChange({ season_template: event.currentTarget.value })}
      />
      <TextInput
        label="文件模板"
        description="一个文件里有多集时，${episodeNr2} 写成 01E02"
        value={output.file_template}
        onChange={(event) => onChange({ file_template: event.currentTarget.value })}
      />
      <Group grow align="flex-start">
        <TextInput
          label="剧目录名空格换成"
          placeholder="不换"
          maxLength={3}
          value={output.show_space}
          onChange={(event) => onChange({ show_space: event.currentTarget.value })}
        />
        <TextInput
          label="季目录名空格换成"
          placeholder="不换"
          maxLength={3}
          value={output.season_space}
          onChange={(event) => onChange({ season_space: event.currentTarget.value })}
        />
        <TextInput
          label="文件名空格换成"
          maxLength={3}
          value={output.file_space}
          onChange={(event) => onChange({ file_space: event.currentTarget.value })}
        />
        <TextInput
          label="冒号换成"
          description="只换半角冒号，全角「：」保留"
          maxLength={3}
          value={output.colon_replacement}
          onChange={(event) => onChange({ colon_replacement: event.currentTarget.value })}
        />
      </Group>
      <Switch
        label="NFO 写入锁定标记"
        checked={output.nfo_write_lockdata}
        onChange={(event) => onChange({ nfo_write_lockdata: event.currentTarget.checked })}
      />
      <Switch
        label="NFO 只写一个制片公司"
        checked={output.nfo_single_studio}
        onChange={(event) => onChange({ nfo_single_studio: event.currentTarget.checked })}
      />
    </Stack>
  );
}
