import type { Alert, Entity, Event, Firms, Graph, Region, ReplayConfig, ReplayScenario, ReplaySnapshot, Status, Timeline, Track, Viewport } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(`${path} failed (${response.status})`);
  return response.json() as Promise<T>;
}

const mapQuery = (viewport: Viewport, limit: number) => new URLSearchParams({ bbox: [viewport.west, viewport.south, viewport.east, viewport.north].map((value) => value.toFixed(4)).join(","), limit: String(limit) });

export const api = {
  status: () => request<Status>("/api/status"),
  events: (viewport: Viewport, limit: number) => request<Event[]>(`/api/events?${new URLSearchParams({ conflict_only: "true", ...Object.fromEntries(mapQuery(viewport, limit)) })}`),
  tracks: (viewport: Viewport, limit: number) => request<Track[]>(`/api/aircraft?${mapQuery(viewport, limit)}`),
  alerts: () => request<Alert[]>("/api/alerts?limit=300"),
  firms: (viewport: Viewport, limit: number) => request<Firms[]>(`/api/firms?${mapQuery(viewport, limit)}`),
  graph: () => request<Graph>("/api/graph?max_nodes=150&max_links=250"),
  entity: (id: string) => request<Entity>(`/api/entity/${encodeURIComponent(id)}`),
  regions: () => request<Region[]>("/api/regions"),
  addRegion: (region: Pick<Region, "lat" | "lon" | "radius_nm"> & { name?: string }) => request<Region>("/api/regions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(region) }),
  removeRegion: (id: string) => request<{ ok: boolean }>(`/api/regions/${id}`, { method: "DELETE" }),
  refresh: () => request<Status>("/api/refresh", { method: "POST" }),
  timeline: (hours: number) => request<Timeline>(`/api/timeline?hours=${hours}`),
  scenarios: () => request<ReplayScenario[]>("/api/replay/scenarios"),
  replayConfig: (id: string) => request<ReplayConfig>(`/api/replay/${id}/config`),
  replayAt: (id: string, time: number) => request<ReplaySnapshot>(`/api/replay/${id}/at?t=${time}`),
  replayTimeline: (id: string) => request<Timeline>(`/api/replay/${id}/timeline`),
};
