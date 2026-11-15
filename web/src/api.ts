export interface SystemInfo {
  name: string;
  version: string;
  data_dir: string;
}

export async function fetchSystemInfo(): Promise<SystemInfo> {
  const response = await fetch("/api/system/info");
  if (!response.ok) {
    throw new Error(`请求失败：HTTP ${response.status}`);
  }
  const body: SystemInfo = await response.json();
  return body;
}
