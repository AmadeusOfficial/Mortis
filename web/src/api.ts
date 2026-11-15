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

export interface AppConfig {
  network: NetworkConfig;
}

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
}

export const fetchSystemInfo = () => request<SystemInfo>("GET", "/api/system/info");
export const fetchSettings = () => request<AppConfig>("GET", "/api/settings");
export const saveSettings = (config: AppConfig) =>
  request<AppConfig>("PUT", "/api/settings", config);
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
