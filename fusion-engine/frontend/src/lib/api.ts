import type { Alert, Entity, Event, Firms, Graph, LivePicture, Region, ReplayConfig, ReplayScenario, ReplaySnapshot, Status, Track } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(`${path} failed (${response.status})`);
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<Status>("/api/status"),
  events: () => request<Event[]>("/api/events?conflict_only=true"),
  tracks: () => request<Track[]>("/api/aircraft"),
  alerts: () => request<Alert[]>("/api/alerts?limit=300"),
  firms: () => request<Firms[]>("/api/firms"),
  graph: () => request<Graph>("/api/graph"),
  entity: (id: string) => request<Entity>(`/api/entity/${encodeURIComponent(id)}`),
  regions: () => request<Region[]>("/api/regions"),
  addRegion: (region: Pick<Region, "lat" | "lon" | "radius_nm"> & { name?: string }) => request<Region>("/api/regions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(region) }),
  removeRegion: (id: string) => request<{ ok: boolean }>(`/api/regions/${id}`, { method: "DELETE" }),
  refresh: () => request<Status>("/api/refresh", { method: "POST" }),
  scenarios: () => request<ReplayScenario[]>("/api/replay/scenarios"),
  replayConfig: (id: string) => request<ReplayConfig>(`/api/replay/${id}/config`),
  replayAt: (id: string, time: number) => request<ReplaySnapshot>(`/api/replay/${id}/at?t=${time}`),
};

export async function livePicture(): Promise<LivePicture> {
  const [status, events, tracks, alerts, firms] = await Promise.all([api.status(), api.events(), api.tracks(), api.alerts(), api.firms().catch(() => [])]);
  return { status, events, tracks, alerts, firms };
}
