import type { AisPicture, AIStatus, Alert, AoiSummary, Assessment, Entity, Event, Evidence, Firms, FusionCandidate, FusionCluster, Graph, LinkPreview, RecordRef, Region, ReplayConfig, ReplayScenario, ReplaySnapshot, Status, Tail, Timeline, Track, Viewport } from "./types";
import { aiDisplayText } from "./display";

async function request<T>(path: string, init?: RequestInit, timeoutMs?: number): Promise<T> {
  const controller = timeoutMs ? new AbortController() : undefined;
  const timer = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    const response = await fetch(path, { ...init, ...(controller ? { signal: controller.signal } : {}) });
    if (!response.ok) {
      let detail: string | undefined;
      try {
        const body: unknown = await response.json();
        if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") detail = body.detail;
      } catch {
        // Keep the status-only fallback for non-JSON proxy responses.
      }
      throw new Error(`${path} failed (${response.status})${detail ? `: ${aiDisplayText(detail)}` : ""}`);
    }
    return await response.json() as T;
  } catch (error) {
    if (controller?.signal.aborted) throw new Error("Evidence analysis timed out. Check the source status and try again shortly.");
    throw error;
  } finally {
    if (timer !== undefined) window.clearTimeout(timer);
  }
}

const mapQuery = (viewport: Viewport, limit: number) => new URLSearchParams({ bbox: [viewport.west, viewport.south, viewport.east, viewport.north].map((value) => value.toFixed(4)).join(","), limit: String(limit) });

export const api = {
  ais: () => request<AisPicture>("/api/ais"),
  status: () => request<Status>("/api/status"),
  events: (viewport: Viewport, limit: number) => request<Event[]>(`/api/events?${new URLSearchParams({ conflict_only: "true", ...Object.fromEntries(mapQuery(viewport, limit)) })}`),
  tracks: (viewport: Viewport, limit: number) => request<Track[]>(`/api/aircraft?${mapQuery(viewport, limit)}`),
  tails: (viewport: Viewport, limit: number) => request<Tail[]>(`/api/aircraft/tails?${mapQuery(viewport, limit)}`),
  alerts: () => request<Alert[]>("/api/alerts?limit=300"),
  firms: (viewport: Viewport, limit: number) => request<Firms[]>(`/api/firms?${mapQuery(viewport, limit)}`),
  graph: () => request<Graph>("/api/graph?max_nodes=150&max_links=250"),
  aiStatus: () => request<AIStatus>("/api/fusion/status"),
  candidates: () => request<FusionCandidate[]>("/api/fusion/candidates?limit=300"),
  assessments: (includeRejected = false) => request<Assessment[]>(`/api/fusion/assessments?include_rejected=${includeRejected}&limit=300`),
  clusters: () => request<FusionCluster[]>("/api/fusion/clusters?limit=100"),
  adjudicate: (left: RecordRef, right: RecordRef, mode: string, time?: number | null, force = false) => request<Assessment>("/api/fusion/adjudicate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ left, right, mode, t: time ?? null, force }) }, 100_000),
  entity: (id: string) => request<Entity>(`/api/entity/${encodeURIComponent(id)}`),
  regions: () => request<Region[]>("/api/regions"),
  analyzeAoi: (id: string, mode: string, time?: number | null) => request<AoiSummary>(`/api/regions/${encodeURIComponent(id)}/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, t: time ?? null }) }, 190_000),
  addRegion: (region: Pick<Region, "lat" | "lon" | "radius_nm"> & { name?: string }) => request<Region>("/api/regions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(region) }),
  removeRegion: (id: string) => request<{ ok: boolean }>(`/api/regions/${id}`, { method: "DELETE" }),
  renameRegion: (id: string, name: string) => request<Region>(`/api/regions/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }),
  refresh: () => request<Status>("/api/refresh", { method: "POST" }),
  timeline: (hours: number) => request<Timeline>(`/api/timeline?hours=${hours}`),
  scenarios: () => request<ReplayScenario[]>("/api/replay/scenarios"),
  replayConfig: (id: string) => request<ReplayConfig>(`/api/replay/${id}/config`),
  replayAt: (id: string, time: number) => request<ReplaySnapshot>(`/api/replay/${id}/at?t=${time}`),
  replayTimeline: (id: string) => request<Timeline>(`/api/replay/${id}/timeline`),
  evidence: (id: string) => request<Evidence>(`/api/replay/${id}/evidence`),
  preview: (url: string) => request<LinkPreview>(`/api/preview?url=${encodeURIComponent(url)}`),
};
