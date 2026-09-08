import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, Crosshair, ExternalLink, Info, Pause, Play, RefreshCw, ScanSearch, Trash2, X } from "lucide-react";
import { api } from "./lib/api";
import type { Alert, Region, ReplaySnapshot, Status, Viewport } from "./lib/types";
import { distanceKm, formatTime, formatWindow } from "./lib/utils";
import { ActivityTimeline } from "./components/ActivityTimeline";
import { OperationalMap, type LayerState, type MapDetail } from "./components/OperationalMap";
import { SourcesDialog } from "./components/SourcesDialog";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Panel } from "./components/ui/panel";

const EMPTY_STATUS: Status = { counts: { events: 0, conflict_events: 0, tracks: 0, military_tracks: 0, alerts: 0 } };
const EMPTY_REPLAY: ReplaySnapshot = { events: [], tracks: [], alerts: [], graph: { nodes: [], links: [] }, counts: EMPTY_STATUS.counts, t_iso: "" };
const WORLD: Viewport = { west: -180, south: -85, east: 180, north: 85, zoom: 2 };
type Detail = MapDetail;
type SourceHealth = NonNullable<Status["sources"]>[string];

function useDebounced<T>(value: T, wait: number) {
  const [result, setResult] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setResult(value), wait);
    return () => window.clearTimeout(timer);
  }, [value, wait]);
  return result;
}

function viewportKey(view: Viewport) { return [view.west, view.south, view.east, view.north, view.zoom].map((value) => value.toFixed(2)).join(":"); }
function limits(zoom: number) { return { events: zoom >= 7 ? 1500 : 3000, tracks: zoom >= 7 ? 1000 : 2500, firms: 1000 }; }

function Metric({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return <div className="min-w-[84px] border-l border-line pl-3"><p className="font-mono text-[9px] uppercase tracking-[.14em] text-muted">{label}</p><p className={`mt-0.5 font-mono text-sm font-semibold ${color ?? "text-ink"}`}>{value}</p></div>;
}

function SourceSignal({ label, source, shown, isFetching, requestError, enabled = true, fallback }: { label: string; source?: SourceHealth; shown?: number; isFetching?: boolean; requestError?: boolean; enabled?: boolean; fallback: string }) {
  const state = !enabled ? "hidden" : requestError ? "error" : source?.state ?? (isFetching ? "starting" : "waiting");
  const words: Record<string, string> = { ready: "LIVE", partial: "PARTIAL", error: "ACTION NEEDED", starting: "STARTING", waiting: "CHECKING", hidden: "LAYER OFF" };
  const tone: Record<string, string> = { ready: "text-command", partial: "text-[#eab85a]", error: "text-critical", starting: "text-[#5cc7da]", waiting: "text-muted", hidden: "text-muted" };
  const dot: Record<string, string> = { ready: "bg-command", partial: "bg-[#eab85a]", error: "bg-critical", starting: "bg-[#5cc7da]", waiting: "bg-muted", hidden: "bg-muted" };
  const total = source?.count;
  const view = shown === undefined || shown === total ? "" : ` · ${shown.toLocaleString()} in view`;
  const detail = !enabled ? "Map layer disabled" : requestError ? "Map query unavailable" : source?.detail ?? fallback;
  return <div className="min-w-[188px] border-l border-line px-3 py-0.5" title={source?.updated ? `Last source update: ${source.updated}` : detail}>
    <div className="flex items-center gap-2"><span className={`h-1.5 w-1.5 rounded-full ${dot[state]}`} /><span className="font-semibold text-ink">{label}</span><span className={`font-mono text-[9px] tracking-wide ${tone[state]}`}>{words[state]}</span></div>
    <p className="mt-0.5 max-w-[260px] truncate text-[10px] text-muted">{total === undefined ? "–" : total.toLocaleString()} records{view} · {detail}</p>
  </div>;
}

function Inspector({ detail }: { detail: Detail | null }) {
  return <Panel className="min-h-[150px] overflow-auto p-3 scrollbar"><h2 className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Inspector</h2>{detail ? <div className="space-y-1 text-xs leading-relaxed"><p className="font-semibold text-ink">{detail.title}</p>{detail.lines.map((line, index) => <p key={index} className="text-muted">{line}</p>)}{detail.href && <a className="inline-block pt-1 font-mono text-[10px] uppercase tracking-wide text-command hover:underline" href={detail.href} target="_blank" rel="noreferrer">{detail.hrefLabel ?? "Open source"} ↗</a>}</div> : <p className="text-xs text-muted">Select an alert or map element to inspect its operational context.</p>}</Panel>;
}

function telegramPostId(href: string) {
  try {
    const parts = new URL(href).pathname.split("/").filter(Boolean);
    const [channel, post] = parts[0] === "s" ? parts.slice(1) : parts;
    return channel && /^\d+$/.test(post ?? "") ? `${channel}/${post}` : undefined;
  } catch { return undefined; }
}

function TelegramPostEmbed({ post, setLoading }: { post: string; setLoading: (value: boolean) => void }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const target = element.current;
    if (!target) return;
    target.replaceChildren();
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute("data-telegram-post", post);
    script.setAttribute("data-width", "100%");
    script.setAttribute("data-color", "2AABEE");
    script.onload = () => setLoading(false);
    target.appendChild(script);
    return () => target.replaceChildren();
  }, [post, setLoading]);
  return <div ref={element} className="h-full overflow-auto p-2 scrollbar" />;
}

function blockedEmbedProvider(href: string) {
  try {
    const host = new URL(href).hostname;
    return host === "firms.modaps.eosdis.nasa.gov" ? "NASA FIRMS" : undefined;
  } catch { return undefined; }
}

function SourceRecord({ detail, provider }: { detail: Detail; provider: string }) {
  return <div className="h-full overflow-auto p-4 text-xs scrollbar"><p className="font-mono text-[10px] uppercase tracking-[.14em] text-command">{provider} source record</p><h3 className="mt-2 text-sm font-semibold text-ink">{detail.title}</h3><div className="mt-4 space-y-2 border-l-2 border-command/60 pl-3">{detail.lines.map((line, index) => <p key={index} className="text-muted">{line}</p>)}</div><p className="mt-5 text-[11px] leading-relaxed text-muted">{provider} blocks third-party framing in Firefox. This panel shows the record retrieved by the engine; use External to browse the provider’s map.</p></div>;
}

function SourceViewer({ detail, onClose }: { detail: Detail; onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  useEffect(() => setLoading(true), [detail.href]);
  if (!detail.href) return null;
  const telegramPost = telegramPostId(detail.href);
  const blockedProvider = blockedEmbedProvider(detail.href);
  return <Panel className="absolute inset-0 z-20 flex min-h-0 flex-col overflow-hidden border-command/60 bg-canvas shadow-2xl">
    <header className="flex items-center gap-2 border-b border-line px-3 py-2"><div className="min-w-0"><p className="font-mono text-[9px] uppercase tracking-[.14em] text-command">Source viewer</p><h2 className="truncate text-xs font-semibold text-ink">{detail.title}</h2></div><a className="ml-auto inline-flex h-7 items-center gap-1 border border-line px-2 font-mono text-[9px] uppercase text-muted hover:text-ink" href={detail.href} target="_blank" rel="noreferrer"><ExternalLink size={11} /> External</a><button aria-label="Close source viewer" onClick={onClose} className="grid h-7 w-7 place-items-center border border-line text-muted hover:text-ink"><X size={14} /></button></header>
    <div className="relative min-h-0 flex-1 bg-black/20">{loading && !blockedProvider && <div className="pointer-events-none absolute inset-0 grid place-items-center font-mono text-[10px] uppercase tracking-wide text-muted">Loading source…</div>}{telegramPost ? <TelegramPostEmbed post={telegramPost} setLoading={setLoading} /> : blockedProvider ? <SourceRecord detail={detail} provider={blockedProvider} /> : <iframe title={detail.hrefLabel ?? detail.title} src={detail.href} onLoad={() => setLoading(false)} className="relative h-full w-full border-0 bg-panel" />}</div>
    <p className="border-t border-line px-3 py-1.5 font-mono text-[9px] text-muted">{telegramPost ? "Telegram’s official post embed." : blockedProvider ? `${blockedProvider} record preview · provider map opens externally.` : "If a provider blocks embedding, use External."}</p>
  </Panel>;
}

function AlertQueue({ alerts, onSelect }: { alerts: Alert[]; onSelect: (alert: Alert) => void }) {
  return <Panel className="flex min-h-0 flex-1 flex-col overflow-hidden"><div className="flex items-center justify-between border-b border-line px-3 py-2"><h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Ranked correlations</h2><Badge><AlertTriangle size={11} /> {alerts.length}</Badge></div><div className="overflow-auto scrollbar"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-panel text-[10px] uppercase tracking-wide text-muted"><tr><th className="px-3 py-2">Score</th><th className="px-2 py-2">Aircraft</th><th className="px-2 py-2">Event</th><th className="px-3 py-2">km</th></tr></thead><tbody>{alerts.map((alert) => <tr key={`${alert.aircraft_id}-${alert.event_id}`} onClick={() => onSelect(alert)} className="cursor-pointer border-t border-line/70 hover:bg-white/[.04]"><td className="px-3 py-2 font-mono font-bold text-command">{alert.score.toFixed(2)}</td><td className="max-w-[92px] truncate px-2 py-2">{alert.aircraft_label}</td><td className="max-w-[180px] truncate px-2 py-2 text-muted">{alert.event_label}</td><td className="px-3 py-2 font-mono text-muted">{alert.distance_km}</td></tr>)}{alerts.length === 0 && <tr><td className="px-3 py-5 text-muted" colSpan={4}>No active correlations.</td></tr>}</tbody></table></div></Panel>;
}

function Regions({ regions, filterAoi, drawing, draft, onToggleFilter, onToggleDrawing, onNameChange, onSave, onCancel, onRemove }: { regions: Region[]; filterAoi: boolean; drawing: boolean; draft: Omit<Region, "id"> | null; onToggleFilter: () => void; onToggleDrawing: () => void; onNameChange: (value: string) => void; onSave: () => void; onCancel: () => void; onRemove: (id: string) => void }) {
  return <Panel className="p-3"><div className="flex items-center gap-2"><h2 className="mr-auto font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Areas of interest</h2><Button size="sm" variant={drawing ? "critical" : "outline"} onClick={onToggleDrawing}>{drawing ? "Cancel" : <><Crosshair size={13} /> Draw</>}</Button></div><label className="mt-2 flex cursor-pointer items-center gap-2 text-[11px] text-muted"><input type="checkbox" checked={filterAoi} onChange={onToggleFilter} /> Filter view to AOIs</label>{drawing && <p className="mt-2 border-l-2 border-command pl-2 text-[11px] text-command">Press and drag on the map to define a 5–250 nm circle.</p>}{draft && <div className="mt-3 space-y-2 border border-command/50 bg-command/5 p-2"><p className="font-mono text-[10px] uppercase text-command">New AOI · {draft.radius_nm} nm</p><input autoFocus value={draft.name} onChange={(event) => onNameChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onSave(); if (event.key === "Escape") onCancel(); }} className="h-8 w-full border border-line bg-canvas px-2 text-xs text-ink outline-none focus:border-command" /><div className="flex gap-2"><Button size="sm" onClick={onSave}>Save</Button><Button size="sm" variant="ghost" onClick={onCancel}>Discard</Button></div></div>}<div className="mt-3 max-h-28 overflow-auto scrollbar"><table className="w-full text-left text-[11px]"><tbody>{regions.map((region) => <tr key={region.id} className="border-t border-line/70"><td className="py-1.5 text-ink">{region.name}</td><td className="py-1.5 font-mono text-muted">{region.radius_nm} nm</td><td className="py-1.5 text-right"><button aria-label={`Remove ${region.name}`} onClick={() => onRemove(region.id)} className="text-muted hover:text-critical"><Trash2 size={13} /></button></td></tr>)}</tbody></table></div></Panel>;
}

export function App() {
  const client = useQueryClient();
  const [mode, setMode] = useState("live"); const [viewport, setViewport] = useState(WORLD); const view = useDebounced(viewport, 250); const max = limits(view.zoom); const key = viewportKey(view);
  const [layers, setLayers] = useState<LayerState>({ events: true, tracks: true, firms: true, sar: true, links: true, imagery: false }); const [timelineHours, setTimelineHours] = useState(24); const [sourcesOpen, setSourcesOpen] = useState(false);
  const [replayTime, setReplayTime] = useState<number | null>(null); const [playing, setPlaying] = useState(false); const [speed, setSpeed] = useState(300); const [filterAoi, setFilterAoi] = useState(false); const [drawing, setDrawing] = useState(false); const [draft, setDraft] = useState<Omit<Region, "id"> | null>(null); const [detail, setDetail] = useState<Detail | null>(null); const [sourceViewer, setSourceViewer] = useState<Detail | null>(null); const [focus, setFocus] = useState<[number, number, number] | undefined>();
  const live = mode === "live"; const polling = 60_000;
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, enabled: live, refetchInterval: live ? polling : false });
  const events = useQuery({ queryKey: ["events", key, max.events], queryFn: () => api.events(view, max.events), enabled: live && layers.events, refetchInterval: live && layers.events ? polling : false, placeholderData: keepPreviousData });
  const tracks = useQuery({ queryKey: ["tracks", key, max.tracks], queryFn: () => api.tracks(view, max.tracks), enabled: live && layers.tracks, refetchInterval: live && layers.tracks ? polling : false, placeholderData: keepPreviousData });
  const firms = useQuery({ queryKey: ["firms", key, max.firms], queryFn: () => api.firms(view, max.firms), enabled: live && layers.firms, refetchInterval: live && layers.firms ? polling : false, placeholderData: keepPreviousData });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, enabled: live, refetchInterval: live ? polling : false, placeholderData: keepPreviousData });
  const liveTimeline = useQuery({ queryKey: ["timeline", timelineHours], queryFn: () => api.timeline(timelineHours), enabled: live, refetchInterval: live ? polling : false, placeholderData: keepPreviousData });
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios }); const config = useQuery({ queryKey: ["replay-config", mode], queryFn: () => api.replayConfig(mode), enabled: !live }); const snapshot = useQuery({ queryKey: ["replay", mode, replayTime], queryFn: () => api.replayAt(mode, replayTime!), enabled: !live && replayTime !== null, placeholderData: keepPreviousData }); const replayTimeline = useQuery({ queryKey: ["replay-timeline", mode], queryFn: () => api.replayTimeline(mode), enabled: !live, staleTime: Infinity }); const regions = useQuery({ queryKey: ["regions"], queryFn: api.regions });
  const addRegion = useMutation({ mutationFn: api.addRegion, onSuccess: () => { client.invalidateQueries({ queryKey: ["regions"] }); setDraft(null); setDrawing(false); } }); const removeRegion = useMutation({ mutationFn: api.removeRegion, onSuccess: () => client.invalidateQueries({ queryKey: ["regions"] }) }); const refresh = useMutation({ mutationFn: api.refresh, onSuccess: () => ["status", "events", "tracks", "firms", "alerts", "timeline"].forEach((queryKey) => client.invalidateQueries({ queryKey: [queryKey] })) });
  useEffect(() => { if (config.data && replayTime === null) { setReplayTime(Math.min(config.data.t_max, config.data.t_min + 43_200)); setFocus([config.data.scenario.center[0], config.data.scenario.center[1], config.data.scenario.zoom]); } }, [config.data, replayTime]);
  useEffect(() => { if (!playing || !config.data) return; const timer = window.setInterval(() => setReplayTime((time) => { const next = Math.min(config.data!.t_max, (time ?? config.data!.t_min) + speed); if (next >= config.data!.t_max) setPlaying(false); return next; }), 1000); return () => window.clearInterval(timer); }, [playing, speed, config.data]);
  const replay = snapshot.data ?? EMPTY_REPLAY;
  const records = live ? { events: events.data ?? [], tracks: tracks.data ?? [], alerts: alerts.data ?? [], firms: firms.data ?? [], sar: [], tails: [], sarCore: [] } : { events: replay.events, tracks: replay.tracks, alerts: replay.alerts, firms: replay.firms ?? [], sar: replay.sar ?? [], tails: replay.tails ?? [], sarCore: replay.sar_core ?? [] };
  const currentStatus: Status = live ? status.data ?? EMPTY_STATUS : { counts: replay.counts, updated: replay.t_iso, store: "replay" };
  const queue = useMemo(() => records.alerts.filter((alert) => !filterAoi || (regions.data ?? []).length === 0 || (regions.data ?? []).some((region) => distanceKm(region.lat, region.lon, alert.lat, alert.lon) <= region.radius_nm * 1.852)), [records.alerts, filterAoi, regions.data]);
  const selectMap = useCallback((next: Detail) => { setDetail(next); setSourceViewer(next.href ? next : null); }, []);
  const selectAlert = useCallback((alert: Alert) => { const event = records.events.find((item) => item.id === alert.event_id); const next = { title: `Correlation score ${alert.score.toFixed(3)}`, lines: [alert.reason ?? "Correlation", `Aircraft ${alert.aircraft_label}`, `Event ${alert.event_label}`, `Δ ${alert.distance_km} km · Δt ${alert.dt_min ?? "–"} min`], href: event?.url, hrefLabel: "Open source article" }; setFocus([alert.lat, alert.lon, 8]); setDetail(next); setSourceViewer(next.href ? next : null); }, [records.events]);
  const replaySar = replay.sar_scene ? `Sentinel-1: ${replay.sar_scene.n} ships · ${replay.sar_scene.label}` : "Sentinel-1: no radar scene within 3 days";

  return <main className="relative z-10 min-h-screen p-2 text-ink lg:h-screen lg:overflow-hidden"><SourcesDialog open={sourcesOpen} onClose={() => setSourcesOpen(false)} /><div className="grid min-h-[calc(100vh-1rem)] grid-rows-[auto_auto_1fr] overflow-hidden border border-line bg-canvas/95 lg:h-[calc(100vh-1rem)]">
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-4 py-3"><div className="mr-2 flex items-center gap-2"><ScanSearch size={18} className="text-command" /><div><h1 className="font-mono text-sm font-bold tracking-[.12em]">MULTI-INT FUSION</h1><p className="font-mono text-[9px] tracking-[.16em] text-muted">COMMAND CONSOLE</p></div></div><Metric label="OSINT" value={`${currentStatus.counts.events} / ${currentStatus.counts.conflict_events}`} color="text-command" /><Metric label="AIR TRACKS" value={`${currentStatus.counts.tracks} / ${currentStatus.counts.military_tracks}`} color="text-[#5cc7da]" /><Metric label="CORRELATIONS" value={currentStatus.counts.alerts} color="text-critical" /><Metric label="UPDATED" value={formatTime(currentStatus.updated)} /><div className="ml-auto flex flex-wrap items-center gap-2"><Button size="sm" variant="outline" onClick={() => setSourcesOpen(true)}><Info size={13} /> Sources</Button><select value={mode} onChange={(event) => { setDetail(null); setDrawing(false); setDraft(null); setPlaying(false); setMode(event.target.value); setReplayTime(null); if (event.target.value === "live") setFocus([35, 10, 2]); }} className="h-9 border border-line bg-canvas px-2 font-mono text-[11px] text-ink"><option value="live">LIVE · GLOBAL STREAM</option>{(scenarios.data ?? []).map((scenario) => <option key={scenario.id} value={scenario.id}>REPLAY · {scenario.title}</option>)}</select>{live && <Button disabled={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw size={14} className={refresh.isPending ? "animate-spin" : ""} /> Refresh</Button>}</div></header>
    {live ? <div className="border-b border-line bg-black/15 py-2 font-mono text-[10px] text-muted"><div className="flex items-center gap-3 px-4"><Badge className="border-command/40 text-command"><Activity size={11} /> Live sources</Badge><span>GDELT window {formatWindow(currentStatus.gdelt_window)}</span><span className="hidden xl:inline">{max.events} OSINT / {max.tracks} ADS-B cap</span><select value={timelineHours} onChange={(event) => setTimelineHours(Number(event.target.value))} className="ml-auto border border-line bg-canvas px-1 text-[10px] text-ink"><option value={1}>1 hour</option><option value={6}>6 hours</option><option value={24}>24 hours</option><option value={0}>All history</option></select></div><div className="flex flex-wrap gap-y-2 px-1 pt-2"><SourceSignal label="GDELT · OSINT" source={currentStatus.sources?.gdelt} shown={records.events.length} isFetching={events.isFetching || status.isFetching} requestError={events.isError} enabled={layers.events} fallback="Geocoded news and event records" /><SourceSignal label="ADS-B · AIRCRAFT" source={currentStatus.sources?.adsb} shown={records.tracks.length} isFetching={tracks.isFetching || status.isFetching} requestError={tracks.isError} enabled={layers.tracks} fallback="Live aircraft positions" /><SourceSignal label="FIRMS · THERMAL" source={currentStatus.sources?.firms} shown={records.firms.length} isFetching={firms.isFetching || status.isFetching} requestError={firms.isError} enabled={layers.firms} fallback="NASA VIIRS thermal anomalies" /><SourceSignal label="TELEGRAM · OSINT" source={currentStatus.sources?.telegram} isFetching={status.isFetching} fallback="Public geolocated channel previews" /><SourceSignal label="FUSION · ALERTS" source={currentStatus.sources?.fusion} shown={records.alerts.length} isFetching={alerts.isFetching || status.isFetching} requestError={alerts.isError} fallback="Spatial and temporal correlations" /></div></div> : <div className="flex flex-wrap items-center gap-3 border-b border-line bg-black/15 px-4 py-2"><Badge className="border-command/40 text-command">Replay</Badge><span className="max-w-md truncate text-xs text-muted">{config.data?.scenario.notes ?? "Loading scenario…"}</span><Button size="sm" variant="outline" onClick={() => setPlaying((value) => !value)}>{playing ? <Pause size={13} /> : <Play size={13} />}{playing ? "Pause" : "Play"}</Button><select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} className="h-7 border border-line bg-canvas px-1 font-mono text-[10px] text-ink"><option value={60}>1 min/s</option><option value={300}>5 min/s</option><option value={900}>15 min/s</option></select><input className="min-w-32 flex-1 accent-[hsl(var(--command))]" type="range" min={config.data?.t_min ?? 0} max={config.data?.t_max ?? 1} step={60} value={replayTime ?? 0} onChange={(event) => setReplayTime(Number(event.target.value))} /><span className="font-mono text-[11px] text-ink">{replay.t_iso ? `${replay.t_iso.slice(0, 16).replace("T", " ")} UTC` : "Loading…"}</span><span className="text-[10px] text-muted">{replaySar}</span></div>}
    <div className="grid min-h-0 gap-2 p-2 lg:grid-cols-[minmax(0,1fr)_390px] lg:grid-rows-[minmax(0,1fr)_300px]"><Panel className="relative min-h-[440px] overflow-hidden lg:row-span-2"><OperationalMap {...records} regions={regions.data ?? []} layers={layers} viewport={viewport} filterAoi={filterAoi} drawing={drawing} focus={focus} satelliteDay={config.data?.scenario.day} replayBounds={live ? undefined : config.data?.scenario.bbox} onViewport={setViewport} onLayerToggle={(layer) => setLayers((value) => ({ ...value, [layer]: !value[layer] }))} onDraft={(value) => { setDraft(value); setDrawing(false); }} onSelect={selectMap} /><div className="pointer-events-none absolute bottom-3 left-3 border border-line bg-panel/95 px-2 py-1 font-mono text-[10px] uppercase tracking-wide text-muted">Canvas map · clusters expand on click</div></Panel><aside className="relative flex min-h-0 flex-col gap-2"><Regions regions={regions.data ?? []} filterAoi={filterAoi} drawing={drawing} draft={draft} onToggleFilter={() => setFilterAoi((value) => !value)} onToggleDrawing={() => { setDrawing((value) => !value); setDraft(null); }} onNameChange={(name) => setDraft((value) => value ? { ...value, name } : value)} onSave={() => { if (draft) addRegion.mutate({ lat: draft.lat, lon: draft.lon, radius_nm: draft.radius_nm, name: draft.name }); }} onCancel={() => setDraft(null)} onRemove={(id) => removeRegion.mutate(id)} /><AlertQueue alerts={queue} onSelect={selectAlert} /><Inspector detail={detail} />{sourceViewer && <SourceViewer detail={sourceViewer} onClose={() => setSourceViewer(null)} />}</aside><Panel className="relative min-h-[280px] overflow-hidden"><div className="pointer-events-none absolute left-3 top-3 z-10"><h2 className="font-mono text-[10px] uppercase tracking-[.14em] text-muted">Multi-source activity timeline</h2></div><ActivityTimeline timeline={live ? liveTimeline.data : replayTimeline.data} activeTime={live ? undefined : replayTime} /></Panel></div>
  </div></main>;
}
