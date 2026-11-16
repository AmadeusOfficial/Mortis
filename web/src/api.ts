export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
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
    throw new ApiError(errorMessage(data, response.status), response.status);
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
  images: MovieImages;
  output: MovieOutputConfig;
}

export interface TvImages {
  poster: boolean;
  fanart: boolean;
  clearlogo: boolean;
  keyart: boolean;
  season_poster: boolean;
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
  show_missing_episodes: boolean;
  show_missing_specials: boolean;
  images: TvImages;
  output: TvOutputConfig;
}

export interface TasksConfig {
  concurrency: number;
  keep_days: number;
}

export interface AppConfig {
  network: NetworkConfig;
  files: FilesConfig;
  movie: MovieScrapeConfig;
  tv: TvScrapeConfig;
  tasks: TasksConfig;
}

export type SettingsSection = "network" | "files" | "movie" | "tv" | "tasks";

export type SecretKey = "tmdb_api_key" | "proxy_username" | "proxy_password";
export type SecretsStatus = Record<SecretKey, boolean>;
export type SecretsUpdate = Partial<Record<SecretKey, string | null>>;

export interface TmdbCheck {
  ok: boolean;
  message: string;
}

export interface DataType {
  id: string;
  name: string;
  structure: string;
}

export interface Library {
  id: number;
  name: string;
  data_type_id: string;
  data_type_name: string;
  automation: string;
  is_auto_scrape: boolean;
  item_count: number;
  file_count: number;
  paths: string[];
  created_at: string;
  updated_at: string;
}

export interface LibraryCreate {
  name: string;
  data_type_id: string;
  paths: string[];
}

export interface LibraryUpdate {
  name?: string;
  paths?: string[];
  is_auto_scrape?: boolean;
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
  child_count: number;
  child_status: Partial<Record<TaskStatus, number>>;
  progress: TaskProgress | null;
}

export interface TaskDetail extends TaskRow {
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
}

export interface TaskPage {
  items: TaskRow[];
  total: number;
}

export type TaskAction = "cancel" | "retry" | "delete";

export type WallSort = "title" | "release_date" | "created_at";
export type SortOrder = "asc" | "desc";

export interface WallItem {
  id: number;
  title: string;
  subtitle: string | null;
  year: number | null;
  poster_url: string | null;
  is_scraped: boolean;
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
  has_file: boolean;
  still_url: string | null;
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
  show: { id: number; title: string; poster_url: string | null } | null;
  // 季「第 1 季」，集「第 1 季 第 2 集」；其他条目为 null。
  position: string | null;
  // 只有电视剧的剧才非空。
  seasons: SeasonInfo[];
  files: { id: number; path: string; file_kind: string; size: number | null }[];
  scraped_at: string | null;
  sections: { name: string; fields: FieldOut[] }[];
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
export const fetchSecretsStatus = () => request<SecretsStatus>("GET", "/api/secrets");
export const saveSecrets = (update: SecretsUpdate) =>
  request<SecretsStatus>("PUT", "/api/secrets", update);
export const checkTmdb = () => request<TmdbCheck>("POST", "/api/settings/check-tmdb");
export const fetchDataTypes = () => request<DataType[]>("GET", "/api/data-types");
export const fetchLibraries = () => request<Library[]>("GET", "/api/libraries");
export const createLibrary = (body: LibraryCreate) =>
  request<Library>("POST", "/api/libraries", body);
export const updateLibrary = (id: number, body: LibraryUpdate) =>
  request<Library>("PATCH", `/api/libraries/${id}`, body);
export const deleteLibrary = (id: number) => request<void>("DELETE", `/api/libraries/${id}`);
export const fetchLibrary = (id: number) => request<Library>("GET", `/api/libraries/${id}`);
export const organizeLibrary = (id: number, scrape: boolean | null = null) =>
  request<Enqueued>("POST", `/api/libraries/${id}/organize`, { scrape });
export const scrapeLibrary = (id: number, force = false) =>
  request<Enqueued>("POST", `/api/libraries/${id}/scrape`, { force });

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
export const clearFinishedTasks = () =>
  request<{ affected: number }>("POST", "/api/tasks/clear-finished");

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

export const fetchWall = (libraryId: number, q: string, sort: WallSort, order: SortOrder) => {
  const params = new URLSearchParams({ sort, order });
  if (q.trim() !== "") {
    params.set("q", q.trim());
  }
  return request<WallItem[]>("GET", `/api/libraries/${libraryId}/items?${params}`);
};
export const fetchItem = (id: number) => request<ItemDetail>("GET", `/api/items/${id}`);
export const updateItemFields = (id: number, values: Record<string, unknown>) =>
  request<ItemDetail>("PATCH", `/api/items/${id}/fields`, { values });
export const updateItemLocks = (id: number, locked: string[]) =>
  request<ItemDetail>("PUT", `/api/items/${id}/locks`, { locked });
export const scrapeItem = (id: number, force = true) =>
  request<Enqueued>("POST", `/api/items/${id}/scrape`, { force });
export const rematchItem = (id: number, tmdbId: number) =>
  request<Enqueued>("POST", `/api/items/${id}/rematch`, { tmdb_id: tmdbId });
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
  const children = (task.child_status.queued ?? 0) + (task.child_status.running ?? 0);
  return task.status === "queued" || task.status === "running" || children > 0;
}
