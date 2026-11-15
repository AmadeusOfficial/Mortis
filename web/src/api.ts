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
}

export interface TasksConfig {
  concurrency: number;
  keep_days: number;
}

export interface AppConfig {
  network: NetworkConfig;
  files: FilesConfig;
  movie: MovieScrapeConfig;
  tasks: TasksConfig;
}

export type SettingsSection = "network" | "files" | "movie" | "tasks";

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

export interface ItemDetail {
  id: number;
  library_id: number;
  library_name: string;
  kind: "work" | "collection";
  title: string;
  subtitle: string | null;
  year: number | null;
  poster_url: string | null;
  fanart_url: string | null;
  external_ids: Record<string, string>;
  collection: { id: number; title: string; poster_url: string | null } | null;
  parts: WallItem[];
  files: { id: number; path: string; file_kind: string; size: number | null }[];
  scraped_at: string | null;
  sections: { name: string; fields: FieldOut[] }[];
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
export const searchTmdbMovies = (query: string, year: number | null) => {
  const params = new URLSearchParams({ query });
  if (year !== null) {
    params.set("year", String(year));
  }
  return request<MovieHit[]>("GET", `/api/tmdb/search/movie?${params}`);
};

export function isTaskBusy(task: TaskRow): boolean {
  const children = (task.child_status.queued ?? 0) + (task.child_status.running ?? 0);
  return task.status === "queued" || task.status === "running" || children > 0;
}
