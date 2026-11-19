export class ApiError extends Error {
  readonly status: number;
  // 后端返回的整份 JSON，409 候选这类带额外内容的错误要用。
  readonly data: unknown;

  constructor(message: string, status: number, data: unknown = undefined) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

function errorMessage(data: unknown, status: number): string {
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detail: unknown = data.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((entry: unknown) =>
          typeof entry === "object" && entry !== null && "msg" in entry
            ? String(entry.msg)
            : String(entry),
        )
        .join("；");
    }
  }
  return `请求失败：HTTP ${status}`;
}

// body 是 Blob（上传的文件）时原样发送，Content-Type 用文件自己的类型。
async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let response: Response;
  const raw = body instanceof Blob;
  try {
    response = await fetch(url, {
      method,
      headers:
        body === undefined
          ? undefined
          : { "Content-Type": raw ? body.type || "application/octet-stream" : "application/json" },
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new ApiError("连不上后端", 0);
    }
    throw error;
  }
  const text = await response.text();
  let data: unknown = undefined;
  if (text !== "") {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined;
    }
  }
  if (!response.ok) {
    throw new ApiError(errorMessage(data, response.status), response.status, data);
  }
  return data as T;
}

export interface SystemInfo {
  name: string;
  version: string;
  data_dir: string;
}

export interface NetworkConfig {
  proxy_host: string;
  proxy_port: number;
  ignore_cert_errors: boolean;
}

export interface FilesConfig {
  video_extensions: string[];
  subtitle_extensions: string[];
}

export interface MovieImages {
  poster: boolean;
  fanart: boolean;
  clearlogo: boolean;
  keyart: boolean;
  banner: boolean;
  clearart: boolean;
  discart: boolean;
  thumb: boolean;
  extrafanart: boolean;
  extrafanart_count: number;
  collection_poster: boolean;
  collection_fanart: boolean;
  actor_images: boolean;
}

export interface MovieOutputConfig {
  organize_after_scrape: boolean;
  organize_rename: boolean;
  organize_write_files: boolean;
  nfo_names: ("movie" | "filename")[];
  image_style: "filename" | "plain";
  collection_image_names: boolean;
  actor_images_folder: string;
  folder_template: string;
  file_template: string;
  folder_space: string;
  file_space: string;
  colon_replacement: string;
  first_char_number: string;
  nfo_write_lockdata: boolean;
  nfo_single_studio: boolean;
}

export interface MovieScrapeConfig {
  language: string;
  certification_country: string;
  include_adult: boolean;
  match_threshold: number;
  skip_folders: string[];
  poster_size: string;
  fanart_size: string;
  prefer_textless_fanart: boolean;
  detect_aspect_ratio: boolean;
  images: MovieImages;
  output: MovieOutputConfig;
}

export interface TvImages {
  poster: boolean;
  fanart: boolean;
  clearlogo: boolean;
  keyart: boolean;
  banner: boolean;
  clearart: boolean;
  thumb: boolean;
  characterart: boolean;
  season_poster: boolean;
  season_banner: boolean;
  season_thumb: boolean;
  episode_thumb: boolean;
  actor_images: boolean;
}

export interface TvOutputConfig {
  organize_after_scrape: boolean;
  organize_rename: boolean;
  organize_write_files: boolean;
  show_template: string;
  season_template: string;
  file_template: string;
  show_space: string;
  season_space: string;
  file_space: string;
  colon_replacement: string;
  first_char_number: string;
  nfo_write_lockdata: boolean;
  nfo_single_studio: boolean;
}

export interface TvScrapeConfig {
  language: string;
  certification_country: string;
  include_adult: boolean;
  match_threshold: number;
  skip_folders: string[];
  poster_size: string;
  fanart_size: string;
  still_size: string;
  prefer_textless_fanart: boolean;
  detect_aspect_ratio: boolean;
  show_missing_episodes: boolean;
  show_missing_specials: boolean;
  images: TvImages;
  output: TvOutputConfig;
}

export interface TasksConfig {
  concurrency: number;
  keep_days: number;
}

// 插件数据类型的类型级设置：识别阈值和扫描时跳过的目录。
export interface PluginTypeConfig {
  match_threshold: number;
  skip_folders: string[];
}

export type TypeSection = MovieScrapeConfig | TvScrapeConfig | PluginTypeConfig;

export interface TypesConfig {
  movie: MovieScrapeConfig;
  tv: TvScrapeConfig;
  anime: TvScrapeConfig;
  // 插件类型另外按 id 存一节；后端没写过时不在返回里。
  [typeId: string]: TypeSection;
}

export interface AppConfig {
  network: NetworkConfig;
  files: FilesConfig;
  tasks: TasksConfig;
  types: TypesConfig;
  // 每个来源或插件自己的设置，按插件 id 分节。
  sources: Record<string, Record<string, unknown>>;
  plugins: { disabled: string[] };
  // 默认播放插件的 id；空字符串表示用第一个可用的。
  playback: { player: string };
  ai: AiConfig;
}

// 智能体用的模型接口：OpenAI 兼容的 Chat Completions；key 在密钥里的 ai_api_key。
export interface AiConfig {
  base_url: string;
  model: string;
  max_rounds: number;
}

// 这几节是通用的；每种数据类型的设置在 types 下，用 saveTypeSettings 存。
export type SettingsSection = "network" | "files" | "tasks" | "playback" | "ai";

export function isBuiltinSection(
  value: TypeSection,
): value is MovieScrapeConfig | TvScrapeConfig {
  return "output" in value;
}

export function defaultPluginTypeConfig(): PluginTypeConfig {
  return { match_threshold: 0.75, skip_folders: [] };
}

// 取一个类型的设置；后端没写过这一节时用默认值补齐。
export function typeSection(types: TypesConfig | undefined, dataTypeId: string): TypeSection {
  const value = types?.[dataTypeId];
  if (value !== undefined && isBuiltinSection(value)) {
    return value;
  }
  return { ...defaultPluginTypeConfig(), ...value };
}

export type SecretKey = "tmdb_api_key" | "proxy_username" | "proxy_password" | "ai_api_key";
export type SecretsStatus = Record<SecretKey, boolean>;
export type SecretsUpdate = Partial<Record<SecretKey, string | null>>;

export interface TmdbCheck {
  ok: boolean;
  message: string;
}

export type AiCheck = TmdbCheck;

export interface DataType {
  id: string;
  name: string;
  structure: string;
  // 插件数据类型的插件 id；内置类型为 null。
  plugin_id: string | null;
}

// 插件设置和密钥的表单由后端给的 JSON Schema 生成。
export interface JsonSchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  format?: string;
}

export interface JsonSchema {
  properties?: Record<string, JsonSchemaProperty>;
}

export type PluginCapability = "data_type" | "source" | "player" | "hook";

export interface PluginHook {
  id: string;
  event: string;
  data_types: string[];
  writes: string[];
}

// 设置页里钩子的运行状态。熔断原因和失败记录只在内存里，重启后只剩通用提示。
export interface PluginHookState extends PluginHook {
  disabled: boolean;
  reason: string | null;
  failures: number;
  last_error: string | null;
  runs: number;
}

export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  // 这一条提供什么，可以是任意组合；没加载的简写插件是粗读的结果。
  capabilities: PluginCapability[];
  // 这个包带的来源和播放条目的 id；没有成员的才是自己带设置和开关的条目。
  members: string[];
  hooks: PluginHookState[];
  origin: "builtin" | "user";
  enabled: boolean;
  error: string | null;
  // 覆盖升级之后，磁盘上已是新版而内存里还是旧对象，要重启莫提斯才完全生效。
  restart_pending: boolean;
  // 子来源插件的父数据类型插件 id；顶层插件为 null。
  parent: string | null;
  data_type: string | null;
  settings_schema: JsonSchema | null;
  settings: Record<string, unknown> | null;
  secrets_schema: JsonSchema | null;
  // 每个密钥是否已填，不含原文。
  secrets: Record<string, boolean> | null;
  oauth: boolean;
  // OAuth 应用的 App ID、App Secret 所在的密钥键。
  oauth_client_keys: string[];
  oauth_register_url: string;
  oauth_token_page_url: string;
  logged_in: boolean;
  // Unix 秒
  expires_at: number | null;
  notice: string | null;
  // 只有播放插件有：能不能用，以及实际会启动的程序路径。
  available: boolean | null;
  detected_path: string | null;
  // 只有播放插件有：能打开哪种本地文件，如 video、game。
  file_kinds: string[] | null;
}

// 自己带设置和开关、并且提供某种能力的条目；带成员的包只是容器，不算。
export function providesAlone(plugin: PluginInfo, capability: PluginCapability): boolean {
  return plugin.members.length === 0 && plugin.capabilities.includes(capability);
}

export interface PluginAccount {
  account: { name: string; avatar_url: string | null; url: string | null } | null;
  error: string | null;
}

export interface PluginUpdate {
  enabled?: boolean;
  settings?: Record<string, unknown>;
  secrets?: Record<string, string>;
}

export interface SourceRef {
  id: string;
  name: string;
  builtin?: boolean;
  // 内置来源会带可用状态；不可用的来源照样排进顺序，刮削时跳过。
  available?: boolean;
  reason?: string | null;
}

// 有单独设置页的内置来源：Fanart.tv、TVDB、IMDb、烂番茄。
export interface BuiltinSourceInfo {
  id: string;
  name: string;
  data_types: string[];
  available: boolean;
  reason: string | null;
  settings: Record<string, unknown>;
  // 每个密钥是否已填，不含原文。
  secrets: Record<string, boolean>;
  provider: string | null;
}

// settings 只改给出的键；secrets 里不出现的键不改，空字符串表示清除。
export interface BuiltinSourceUpdate {
  settings?: Record<string, unknown>;
  secrets?: Record<string, string>;
}

export interface ImdbDataset {
  imported_at: string | null;
  count: number | null;
  stale: boolean;
  // 正在排队或运行的更新任务。
  task_id: number | null;
}

export interface OrderEntry {
  source: string;
  excluded: boolean;
}

export interface FieldOrder {
  key: string;
  label: string;
  // false 表示跟着整体顺序。
  custom: boolean;
  order: OrderEntry[];
}

export interface SourceOrders {
  sources: SourceRef[];
  whole: OrderEntry[];
  fields: FieldOrder[];
}

// 来源插件的搜索结果。
export interface SourceHit {
  external_id: string;
  title: string;
  original_title: string | null;
  year: number | null;
  overview: string | null;
  poster_url: string | null;
}

export interface Library {
  id: number;
  name: string;
  data_type_id: string;
  data_type_name: string;
  automation: Automation;
  is_auto_scrape: boolean;
  wall_collections: WallCollections;
  show_missing_episodes: boolean | null;
  item_count: number;
  file_count: number;
  scraped_count: number;
  // 手动条目不刮削，不算待处理。
  manual_count: number;
  issue_count: number;
  sample_posters: string[];
  paths: string[];
  schedule_cron: string | null;
  refresh_running: boolean;
  next_scan_at: string | null;
  watch_state: "watching" | "failed" | null;
  watch_error: string | null;
  created_at: string;
  updated_at: string;
}

export type Automation = "manual" | "watch" | "schedule";

// 电影库海报墙怎么显示系列电影：只显示电影、系列和电影都显示、系列收起。
export type WallCollections = "movies" | "both" | "collapse";

export interface LibraryCreate {
  name: string;
  data_type_id: string;
  paths: string[];
  automation?: Automation;
  schedule_cron?: string | null;
  refresh_running?: boolean;
  wall_collections?: WallCollections;
}

export interface LibraryUpdate {
  name?: string;
  paths?: string[];
  is_auto_scrape?: boolean;
  automation?: Automation;
  schedule_cron?: string | null;
  refresh_running?: boolean;
  wall_collections?: WallCollections;
  show_missing_episodes?: boolean;
}

export interface CronPreview {
  cron: string;
  next_runs: string[];
}

export interface Enqueued {
  task_id: number;
}

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type TaskFilter = "all" | "active" | "failed";

export interface TaskProgress {
  current: number;
  total: number;
  message: string;
}

export interface TaskRow {
  id: number;
  task_type: string;
  type_label: string;
  title: string;
  status: TaskStatus;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  // 直接子任务的个数和状态。
  child_count: number;
  child_status: Partial<Record<TaskStatus, number>>;
  // 根任务是整棵子孙的统计，子任务等于自己的 child_status。
  tree_status: Partial<Record<TaskStatus, number>>;
  priority: number;
  parent_task_id: number | null;
  root_task_id: number | null;
  progress: TaskProgress | null;
}

export interface TaskItemRef {
  id: number;
  title: string;
}

export interface TaskDetail extends TaskRow {
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  // 结果或参数指向的条目；条目已删除时为 null。
  item: TaskItemRef | null;
}

export interface TaskPage {
  items: TaskRow[];
  total: number;
}

export type TaskAction = "cancel" | "retry" | "delete";
export type BulkAction = "cancel_active" | "retry_failed" | "clear_succeeded" | "clear_failed";

export type WallSort = "title" | "release_date" | "created_at" | "updated_at";
export type SortOrder = "asc" | "desc";

export interface WallItem {
  id: number;
  kind: "work" | "collection";
  title: string;
  subtitle: string | null;
  year: number | null;
  poster_url: string | null;
  is_scraped: boolean;
  // 没有任何外部编号的作品，由人在未识别页建的。
  is_manual: boolean;
  issue: string | null;
}

export interface PersonValue {
  id: number;
  name: string;
  original_name: string | null;
  character: string | null;
  photo_url: string | null;
}

export interface RatingValue {
  source: string;
  rating: number;
  max_rating: number;
  vote_count: number | null;
}

export interface ImageValue {
  url: string | null;
  language: string | null;
  width: number | null;
  height: number | null;
}

export interface FieldOut {
  key: string;
  label: string;
  value_type: string;
  stored_in: string;
  value: unknown;
  source: string | null;
  locked: boolean;
  lockable: boolean;
  editable: boolean;
}

export interface EpisodeInfo {
  id: number;
  number: number | null;
  title: string;
  air_date: string | null;
  overview?: string | null;
  runtime?: number | null;
  has_file: boolean;
  // 视频绑在这一集上，可以直接播放；连播两集共用文件时只有绑定的那集是 true。
  playable: boolean;
  still_url: string | null;
  files?: { id: number; path: string; file_kind: string; size: number | null }[];
}

export interface SeasonInfo {
  id: number;
  number: number | null;
  title: string;
  poster_url: string | null;
  episode_count: number;
  file_count: number;
  episodes: EpisodeInfo[];
}

export interface ItemDetail {
  id: number;
  library_id: number;
  data_type_id: string;
  library_name: string;
  kind: "work" | "collection" | "season" | "episode";
  title: string;
  subtitle: string | null;
  year: number | null;
  poster_url: string | null;
  fanart_url: string | null;
  external_ids: Record<string, string>;
  collection: { id: number; title: string; poster_url: string | null } | null;
  parts: WallItem[];
  // 季和集所属的剧；其他条目为 null。
  show: {
    id: number;
    title: string;
    poster_url: string | null;
    logo_url?: string | null;
  } | null;
  // 季「第 1 季」，集「第 1 季 第 2 集」；其他条目为 null。
  position: string | null;
  // 只有电视剧的剧才非空。
  seasons: SeasonInfo[];
  // 剧自己的选择。null 是跟随媒体库。电影和季集为 null。
  show_missing_episodes: boolean | null;
  show_missing_effective: boolean | null;
  files: { id: number; path: string; file_kind: string; size: number | null }[];
  scraped_at: string | null;
  // 季和集跟着所属的剧。
  is_manual: boolean;
  sections: { name: string; fields: FieldOut[] }[];
  // 作品的第一个视频或游戏目录，和从它解析出的标题、年份；没有文件时为 null。
  guess?: FileGuess | null;
}

export interface FileGuess {
  file_id: number;
  file_name: string;
  folder_name: string;
  title: string;
  year: number | null;
}

export interface RenameMove {
  source: string;
  target: string;
}

export interface ArrangePreview {
  item_id: number;
  title: string;
  rename: boolean;
  write: boolean;
  folder: string;
  folder_from: string | null;
  folder_to: string | null;
  make_folder: boolean;
  moves: RenameMove[];
  problems: string[];
  changes: boolean;
}

export interface LibraryArrangePreview {
  count: number;
  rename: boolean;
  write: boolean;
  previews: ArrangePreview[];
}

export interface MovieHit {
  tmdb_id: number;
  title: string;
  original_title: string;
  release_date: string | null;
  year: number | null;
  overview: string;
  poster_url: string | null;
}

export const fetchSystemInfo = () => request<SystemInfo>("GET", "/api/system/info");
export const fetchSettings = () => request<AppConfig>("GET", "/api/settings");
export const saveSettingsSection = <K extends SettingsSection>(name: K, section: AppConfig[K]) =>
  request<AppConfig>("PUT", `/api/settings/${name}`, section);
// 一整个数据类型的设置：内置类型按各自的模型校验，插件类型只有阈值和跳过目录。
export const saveTypeSettings = (typeId: string, value: TypeSection) =>
  request<AppConfig>("PUT", `/api/settings/types/${encodeURIComponent(typeId)}`, value);
export const fetchSecretsStatus = () => request<SecretsStatus>("GET", "/api/secrets");
export const saveSecrets = (update: SecretsUpdate) =>
  request<SecretsStatus>("PUT", "/api/secrets", update);
export const checkTmdb = () => request<TmdbCheck>("POST", "/api/settings/check-tmdb");
// 接口地址和模型名可以是还没保存的草稿；key 总是用已保存的。
export const checkAi = (draft: Pick<AiConfig, "base_url" | "model">) =>
  request<AiCheck>("POST", "/api/settings/check-ai", draft);
export const fetchDataTypes = () => request<DataType[]>("GET", "/api/data-types");
export const fetchLibraries = () => request<Library[]>("GET", "/api/libraries");
export const createLibrary = (body: LibraryCreate) =>
  request<Library>("POST", "/api/libraries", body);
export const updateLibrary = (id: number, body: LibraryUpdate) =>
  request<Library>("PATCH", `/api/libraries/${id}`, body);
export const updateItemMissing = (id: number, showMissingEpisodes: boolean | null) =>
  request<ItemDetail>("PUT", `/api/items/${id}/missing-episodes`, {
    show_missing_episodes: showMissingEpisodes,
  });
export const deleteLibrary = (id: number) => request<void>("DELETE", `/api/libraries/${id}`);
export const fetchLibrary = (id: number) => request<Library>("GET", `/api/libraries/${id}`);
export const organizeLibrary = (id: number, scrape: boolean | null = null) =>
  request<Enqueued>("POST", `/api/libraries/${id}/organize`, { scrape });
export const scrapeLibrary = (id: number, force = false) =>
  request<Enqueued>("POST", `/api/libraries/${id}/scrape`, { force });
export const revealMediaFile = (id: number) =>
  request<void>("POST", `/api/media-files/${id}/reveal`);
export const previewCron = (cron: string) =>
  request<CronPreview>("GET", `/api/schedules/preview?${new URLSearchParams({ cron }).toString()}`);

export const TASK_PAGE_SIZE = 50;
export const fetchTasks = (filter: TaskFilter, offset = 0) => {
  const params = new URLSearchParams({ limit: String(TASK_PAGE_SIZE), offset: String(offset) });
  if (filter !== "all") {
    params.set("status", filter);
  }
  return request<TaskPage>("GET", `/api/tasks?${params}`);
};
export const fetchTask = (id: number) => request<TaskDetail>("GET", `/api/tasks/${id}`);
export const fetchTaskChildren = (id: number) =>
  request<TaskRow[]>("GET", `/api/tasks/${id}/children`);
export const fetchTaskLog = (id: number) =>
  request<{ lines: string[] }>("GET", `/api/tasks/${id}/log?limit=500`);
export const batchTasks = (action: TaskAction, taskIds: number[]) =>
  request<{ affected: number }>("POST", "/api/tasks/batch", { action, task_ids: taskIds });
export const fetchWorker = () => request<{ paused: boolean }>("GET", "/api/tasks/worker");
export const setWorkerPaused = (paused: boolean) =>
  request<{ paused: boolean }>("PUT", "/api/tasks/worker", { paused });
export const bulkTasks = (action: BulkAction) =>
  request<{ affected: number }>("POST", "/api/tasks/bulk", { action });

export interface UnmatchedFile {
  id: number;
  library_id: number;
  library_name: string;
  data_type_id: string;
  path: string;
  size: number | null;
  guess_title: string;
  guess_year: number | null;
  // 电视剧库按剧目录合并成一行，path 是剧目录，这里是目录下没匹配的视频数。
  video_count: number;
}

export const fetchUnmatched = (libraryId: number | null) =>
  request<UnmatchedFile[]>(
    "GET",
    libraryId === null
      ? "/api/media-files/unmatched"
      : `/api/media-files/unmatched?library_id=${libraryId}`,
  );
export const bindMediaFile = (fileId: number, hit: MovieHit) =>
  request<{ item_id: number; task_id: number }>("POST", `/api/media-files/${fileId}/bind`, {
    tmdb_id: hit.tmdb_id,
    title: hit.title,
    original_title: hit.original_title,
    release_date: hit.release_date,
  });

export interface ManualItemCreate {
  title: string;
  original_title?: string;
  release_date?: string;
  overview?: string;
}

export const createManualItem = (fileId: number, body: ManualItemCreate) =>
  request<{ item_id: number }>("POST", `/api/media-files/${fileId}/manual`, body);

export const bindMediaFileToSource = (fileId: number, source: string, hit: SourceHit) =>
  request<{ item_id: number; task_id: number }>("POST", `/api/media-files/${fileId}/bind`, {
    source,
    external_id: hit.external_id,
    title: hit.title,
    original_title: hit.original_title,
  });

export const fetchPlugins = () => request<PluginInfo[]>("GET", "/api/plugins");
export const reloadPlugins = () => request<PluginInfo[]>("POST", "/api/plugins/reload");
export const updatePlugin = (id: string, body: PluginUpdate) =>
  request<PluginInfo>("PUT", `/api/plugins/${id}`, body);
export const enablePluginHook = (pluginId: string, hookId: string) =>
  request<PluginInfo>("POST", `/api/plugins/${pluginId}/hooks/${hookId}/enable`);
export interface PackageFile {
  path: string;
  size: number;
  sha256: string;
}

export interface PackageExisting {
  id: string;
  name: string;
  version: string;
  package_hash: string;
  added: string[];
  removed: string[];
  changed: string[];
}

export interface PackagePreview {
  // 一次性确认令牌，确认安装时带上。
  token: string;
  expires_in: number;
  id: string;
  name: string;
  version: string;
  api_version: number;
  min_mortis_version: string | null;
  capabilities: PluginCapability[];
  sources: { id: string; name: string; data_type: string }[];
  players: { id: string; name: string }[];
  hooks: PluginHook[];
  files: PackageFile[];
  total_size: number;
  package_hash: string;
  // 同 id 的旧版本；全新安装为 null。
  existing: PackageExisting | null;
  needs_restart: boolean;
}

export interface PackageInstalled {
  id: string;
  name: string;
  version: string;
  previous_version: string | null;
  replaced: boolean;
  needs_restart: boolean;
}

// 请求体就是 zip 文件本身，不用 multipart。
export const checkPluginPackage = (file: Blob) =>
  request<PackagePreview>("POST", "/api/plugins/import/check", file);
export const confirmPluginPackage = (token: string, trust: boolean) =>
  request<PackageInstalled>("POST", "/api/plugins/import/confirm", { token, trust });
export const uninstallPlugin = (id: string) =>
  request<{ id: string; name: string }>("DELETE", `/api/plugins/${id}`);
export const fetchPluginAccount = (id: string) =>
  request<PluginAccount>("GET", `/api/plugins/${id}/account`);
export const logoutPlugin = (id: string) => request<PluginInfo>("DELETE", `/api/plugins/${id}/oauth`);
// 浏览器整页跳过去，登录完由后端跳回设置页。
export const pluginLoginUrl = (id: string) => `/api/plugins/${id}/oauth/login`;

export const fetchSources = (dataType: string) =>
  request<SourceRef[]>("GET", `/api/sources?data_type=${encodeURIComponent(dataType)}`);
export const fetchBuiltinSources = () =>
  request<BuiltinSourceInfo[]>("GET", "/api/sources/builtin");
export const updateBuiltinSource = (id: string, body: BuiltinSourceUpdate) =>
  request<BuiltinSourceInfo>("PUT", `/api/sources/builtin/${id}`, body);
export const fetchImdbDataset = () => request<ImdbDataset>("GET", "/api/sources/imdb/dataset");
export const refreshImdbDataset = () =>
  request<Enqueued>("POST", "/api/sources/imdb/dataset/refresh");
export const searchSource = (source: string, query: string) =>
  request<SourceHit[]>(
    "GET",
    `/api/sources/${source}/search?query=${encodeURIComponent(query)}`,
  );

export const fetchSourceOrders = (dataType: string) =>
  request<SourceOrders>("GET", `/api/data-types/${dataType}/source-orders`);
export const saveSourceOrders = (dataType: string, orders: Record<string, OrderEntry[] | null>) =>
  request<SourceOrders>("PUT", `/api/data-types/${dataType}/source-orders`, { orders });
export const resetSourceOrders = (dataType: string) =>
  request<SourceOrders>("POST", `/api/data-types/${dataType}/source-orders/reset`);

export const fetchWall = (
  libraryId: number,
  q: string,
  sort: WallSort,
  order: SortOrder,
  tagIds: number[] = [],
) => {
  const params = new URLSearchParams({ sort, order });
  if (q.trim() !== "") {
    params.set("q", q.trim());
  }
  for (const tagId of tagIds) {
    params.append("tag", String(tagId));
  }
  return request<WallItem[]>("GET", `/api/libraries/${libraryId}/items?${params}`);
};

export interface Shelf {
  tag_id: number;
  name: string;
  count: number;
  items: WallItem[];
}

export const fetchShelves = (libraryId: number, tagIds: number[], sort: WallSort, order: SortOrder) => {
  const params = new URLSearchParams({ tags: tagIds.join(","), sort, order });
  return request<Shelf[]>("GET", `/api/libraries/${libraryId}/shelves?${params}`);
};
export const fetchItem = (id: number) => request<ItemDetail>("GET", `/api/items/${id}`);
export const updateItemFields = (id: number, values: Record<string, unknown>) =>
  request<ItemDetail>("PATCH", `/api/items/${id}/fields`, { values });
export const updateItemLocks = (id: number, locked: string[]) =>
  request<ItemDetail>("PUT", `/api/items/${id}/locks`, { locked });

// 一个图片字段的候选，按这个字段的来源顺序排好；selected 是当前用的那张。
export interface ImageChoice {
  url: string;
  source: string;
  source_name: string;
  language: string | null;
  region: string | null;
  width: number | null;
  height: number | null;
  preview_url: string;
  selected: boolean;
}

const imagePath = (id: number, key: string) =>
  `/api/items/${id}/images/${encodeURIComponent(key)}`;
export const fetchImageChoices = (id: number, key: string) =>
  request<ImageChoice[]>("GET", `${imagePath(id, key)}/choices`);
// 候选里的网址记原来源；候选外的网址当作手动添加。两种都会锁定这个字段。
export const chooseImage = (id: number, key: string, url: string) =>
  request<ItemDetail>("PUT", imagePath(id, key), { url });
export const uploadImage = (id: number, key: string, file: File) =>
  request<ItemDetail>("PUT", `${imagePath(id, key)}/file`, file);

// 库里作品用到的类型（刮削来的和手改的都算），count 是有这个类型的作品数。
export interface Genre {
  id: number;
  name: string;
  count: number;
}

export const fetchLibraryGenres = (libraryId: number) =>
  request<Genre[]>("GET", `/api/libraries/${libraryId}/genres`);
export const scrapeItem = (id: number, force = true) =>
  request<Enqueued>("POST", `/api/items/${id}/scrape`, { force });
export const rematchItem = (id: number, tmdbId: number) =>
  request<Enqueued>("POST", `/api/items/${id}/rematch`, { tmdb_id: tmdbId });
export const rematchItemToSource = (id: number, source: string, externalId: string) =>
  request<Enqueued>("POST", `/api/items/${id}/rematch`, { source, external_id: externalId });
// 给插件类型的条目补一个来源，主编号不变。
export const addItemSource = (id: number, source: string, externalId: string) =>
  request<Enqueued>("POST", `/api/items/${id}/sources`, { source, external_id: externalId });
export interface PlayCandidate {
  file_id: number;
  path: string;
  name: string;
  size: number | null;
  exists: boolean;
  // 游戏条目的候选程序相对游戏文件夹的路径；视频候选和游戏文件夹候选为 null。
  target?: string | null;
}

export interface PlayRequest {
  file_id?: number;
  // 游戏条目：要启动的程序，相对游戏文件夹的路径。
  target?: string;
  // 游戏条目：不用记住的程序，重新给出候选。
  choose?: boolean;
}

export interface PlayResult {
  player: string;
  player_name: string;
  file_id: number;
  path: string;
}

export const playItem = (id: number, body: PlayRequest = {}) =>
  request<PlayResult>("POST", `/api/items/${id}/play`, body);

// 多个视频时后端回 409 和候选，不替人选；其他错误返回 null。
export function playCandidates(error: unknown): PlayCandidate[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) {
    return null;
  }
  const data = error.data;
  if (typeof data === "object" && data !== null && "candidates" in data && Array.isArray(data.candidates)) {
    return data.candidates as PlayCandidate[];
  }
  return null;
}

export interface DeletePreview {
  item_id: number;
  title: string;
  library_id: number;
  folders: string[];
  // 不含 folders 里已经包括的文件。
  files: string[];
  file_count: number;
  total_size: number;
  permanent: boolean;
  // 独占文件夹里会一起删的未登记文件，sample 是前几个的相对路径。
  unregistered: { count: number; total_size: number; sample: string[] };
  reasons: string[];
}

export interface DeleteExpected {
  folders: string[];
  files: string[];
  file_count: number;
  total_size: number;
  permanent: boolean;
  unregistered_count: number;
}

// 预览里确认过的内容原样交回，后端重新算一遍，不一样就 409。
export function deleteExpected(preview: DeletePreview): DeleteExpected {
  return {
    folders: preview.folders,
    files: preview.files,
    file_count: preview.file_count,
    total_size: preview.total_size,
    permanent: preview.permanent,
    unregistered_count: preview.unregistered.count,
  };
}

export const fetchDeletePreview = (id: number) =>
  request<DeletePreview>("GET", `/api/items/${id}/delete-preview`);
export const deleteItem = (id: number, confirmTitle: string, expected: DeleteExpected) =>
  request<{ deleted: boolean; library_id: number }>("DELETE", `/api/items/${id}`, {
    confirm_title: confirmTitle,
    expected,
  });

export const fetchItemArrangePreview = (id: number) =>
  request<ArrangePreview>("GET", `/api/items/${id}/arrange-preview`);
export const arrangeItem = (id: number) => request<Enqueued>("POST", `/api/items/${id}/arrange`);
export const fetchLibraryArrangePreview = (id: number) =>
  request<LibraryArrangePreview>("GET", `/api/libraries/${id}/arrange-preview`);
export const arrangeLibrary = (id: number) =>
  request<Enqueued>("POST", `/api/libraries/${id}/arrange`);
export type SearchKind = "movie" | "tv";

export const searchTmdbMovies = (query: string, year: number | null, kind: SearchKind = "movie") => {
  const params = new URLSearchParams({ query });
  if (year !== null) {
    params.set("year", String(year));
  }
  return request<MovieHit[]>("GET", `/api/tmdb/search/${kind}?${params}`);
};

export function isTaskBusy(task: TaskRow): boolean {
  const children = (task.tree_status.queued ?? 0) + (task.tree_status.running ?? 0);
  return task.status === "queued" || task.status === "running" || children > 0;
}

export interface DirListing {
  path: string;
  // 空字符串表示上一级是盘符列表；null 表示已经在盘符列表。
  parent: string | null;
  dirs: { name: string; path: string }[];
  files: { name: string; path: string }[];
}

export function fetchDirs(path: string, files = false): Promise<DirListing> {
  const query = `path=${encodeURIComponent(path)}${files ? "&files=true" : ""}`;
  return request("GET", `/api/files/dirs?${query}`);
}
