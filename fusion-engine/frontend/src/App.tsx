import { useCallback, useEffect, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Crosshair, ExternalLink, Info, Link2, MapPin, Newspaper, Pause, Plane, Play, Radio, RefreshCw, ScanSearch, Trash2, Users, X } from "lucide-react";
import { api } from "./lib/api";
import type { AIStatus, Assessment, Entity, FusionCandidate, FusionCluster, RecordRef, Region, ReplaySnapshot, Status, Viewport } from "./lib/types";
import { distanceKm, formatTime, formatWindow } from "./lib/utils";
import { ActivityTimeline } from "./components/ActivityTimeline";
import { OperationalMap, type LayerState, type MapDetail } from "./components/OperationalMap";
import { SourcesDialog } from "./components/SourcesDialog";
import { AssessmentResult } from "./components/AssessmentResult";
import { CuratedEvidence } from "./components/CuratedEvidence";
import { SourcePreview } from "./components/SourcePreview";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Panel } from "./components/ui/panel";

const EMPTY_STATUS: Status = { counts: { events: 0, conflict_events: 0, tracks: 0, military_tracks: 0, alerts: 0 } };
const EMPTY_REPLAY: ReplaySnapshot = { t: 0, events: [], tracks: [], alerts: [], graph: { nodes: [], links: [] }, counts: EMPTY_STATUS.counts, t_iso: "" };
const WORLD: Viewport = { west: -180, south: -85, east: 180, north: 85, zoom: 2 };
type Detail = MapDetail & { replayTime?: number };
type AdjudicationRequest = { left: RecordRef; right: RecordRef; mode: string; time: number | null; force?: boolean };
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

function Metric({ label, value, detail, color, help }: { label: string; value: string | number; detail?: string; color?: string; help?: string }) {
  return <div className="min-w-[100px] border-l border-line pl-3" title={help}><p className="font-mono text-[9px] uppercase tracking-[.14em] text-muted">{label}</p><p className={`mt-0.5 font-mono text-sm font-semibold ${color ?? "text-ink"}`}>{value}</p>{detail && <p className="mt-0.5 text-[9px] text-muted">{detail}</p>}</div>;
}

function SourceSignal({ label, markerColor, markerLabel, source, shown, isFetching, requestError, enabled = true, fallback }: { label: string; markerColor: string; markerLabel: string; source?: SourceHealth; shown?: number; isFetching?: boolean; requestError?: boolean; enabled?: boolean; fallback: string }) {
  const state = !enabled ? "hidden" : requestError ? "error" : source?.state ?? (isFetching ? "starting" : "waiting");
  const displayMarkerColor = label === "FUSION · ALERTS" ? "#94c973" : label === "ADS-B · AIRCRAFT" ? "linear-gradient(90deg, #5cc7da 0 50%, #1d4ed8 50% 100%)" : markerColor;
  const displayMarkerLabel = label === "FUSION · ALERTS" ? "Green correlation link" : label === "ADS-B · AIRCRAFT" ? "Light-blue civilian airplane and dark-blue military airplane" : markerLabel;
  const words: Record<string, string> = { ready: "LIVE", partial: "PARTIAL", error: "ACTION NEEDED", starting: "STARTING", waiting: "CHECKING", hidden: "LAYER OFF" };
  const tone: Record<string, string> = { ready: "text-command", partial: "text-[#eab85a]", error: "text-critical", starting: "text-[#5cc7da]", waiting: "text-muted", hidden: "text-muted" };
  const total = source?.count;
  const view = shown === undefined || shown === total ? "" : ` · ${shown.toLocaleString()} in view`;
  const detail = !enabled ? "Map layer disabled" : requestError ? "Map query unavailable" : source?.detail ?? fallback;
  return <div className="min-w-[188px] border-l border-line px-3 py-0.5" title={`${displayMarkerLabel} map marker. ${source?.updated ? `Last source update: ${source.updated}` : detail}`}>
    <div className="flex items-center gap-2"><span aria-label={`${displayMarkerLabel} map marker`} className="h-2.5 w-2.5 shrink-0 rounded-full border border-black/30" style={{ background: displayMarkerColor }} /><span className="font-semibold text-ink">{label}</span><span className={`font-mono text-[9px] tracking-wide ${tone[state]}`}>{words[state]}</span></div>
    <p className="mt-0.5 max-w-[260px] truncate text-[10px] text-muted">{total === undefined ? "–" : total.toLocaleString()} records{view} · {detail}</p>
  </div>;
}

function MapKey({ label, color, detail }: { label: string; color: string; detail: string }) {
  return <div className="flex items-center gap-2 border-l border-line px-3 py-0.5 text-[10px]" title={detail}>
    <span aria-label={`${label} map marker`} className="h-2.5 w-2.5 shrink-0 rounded-full border border-black/30" style={{ background: color }} /><span className="font-semibold text-ink">{label}</span><span className="text-muted">Click center to zoom</span>
  </div>;
}

function CorrelationEvidence({ connections }: { connections: NonNullable<Detail["connections"]> }) {
  return <div className="mt-4 border-t border-line pt-3"><div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[.14em] text-command"><Link2 size={12} /> Correlation evidence</div><p className="mt-1 text-[10px] text-muted">Every item directly linked by this proposed correlation.</p><div className="mt-2 space-y-1.5">{connections.map((connection, index) => <div key={`${connection.kind}-${connection.label}-${index}`} className={`flex gap-2 border p-2 ${connection.selected ? "border-command/60 bg-command/5" : "border-line bg-black/10"}`}><span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full ${connection.kind === "event" ? "bg-[#eab85a] text-[#694713]" : "bg-[#5cc7da] text-[#102b33]"}`}>{connection.kind === "event" ? <Newspaper size={13} /> : <Plane size={13} />}</span><div className="min-w-0"><p className="truncate text-[11px] font-semibold text-ink">{connection.label}</p><p className="mt-0.5 text-[10px] leading-snug text-muted">{connection.detail}</p></div></div>)}</div></div>;
}

function GraphEvidence({ entity, loading }: { entity?: Entity; loading: boolean }) {
  if (loading) return <p className="mt-4 font-mono text-[10px] text-muted">Loading connected graph evidence…</p>;
  if (!entity?.neighbors.length) return null;
  const icon = (kind: string) => kind === "event" ? <Newspaper size={13} /> : kind === "aircraft" ? <Plane size={13} /> : kind === "location" ? <MapPin size={13} /> : kind === "actor" ? <Users size={13} /> : <Radio size={13} />;
  const tone = (kind: string) => kind === "event" ? "bg-[#eab85a] text-[#694713]" : kind === "aircraft" ? "bg-[#5cc7da] text-[#102b33]" : kind === "location" ? "bg-[#a78bfa] text-[#25154d]" : kind === "actor" ? "bg-[#94c973] text-[#21371c]" : "bg-[#e879f9] text-[#4e174e]";
  return <div className="mt-4 border-t border-line pt-3"><div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[.14em] text-command"><Link2 size={12} /> Connected graph evidence</div><p className="mt-1 text-[10px] text-muted">All directly related records: sources, actors, locations, aircraft, and other linked facts.</p><div className="mt-2 space-y-1.5">{entity.neighbors.map((node) => { const relations = entity.links.filter((link) => link.source === node.id || link.target === node.id).map((link) => link.kind).filter(Boolean); return <div key={node.id} className="flex gap-2 border border-line bg-black/10 p-2"><span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full ${tone(node.kind)}`}>{icon(node.kind)}</span><div className="min-w-0"><p className="truncate text-[11px] font-semibold text-ink">{node.label}</p><p className="mt-0.5 font-mono text-[9px] uppercase text-muted">{node.kind}{relations.length ? ` · ${relations.join(", ")}` : ""}</p></div></div>; })}</div></div>;
}

function Inspector({ detail, entity, entityLoading = false, expanded = false, onClose, onEmbed }: { detail: Detail | null; entity?: Entity; entityLoading?: boolean; expanded?: boolean; onClose?: () => void; onEmbed?: (d: Detail) => void }) {
  return <Panel className={`${expanded ? "flex min-h-0 flex-1 flex-col overflow-hidden" : "min-h-[150px] overflow-auto scrollbar"} p-3`}><div className="mb-2 flex items-center gap-2"><h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">{expanded ? "Selected source" : "Inspector"}</h2>{expanded && <button aria-label="Close selected source" onClick={onClose} className="ml-auto grid h-6 w-6 place-items-center border border-line text-muted hover:text-ink"><X size={13} /></button>}</div><div className={expanded ? "min-h-0 flex-1 overflow-auto scrollbar" : ""}>{detail ? <div className="space-y-1 text-xs leading-relaxed"><p className="font-semibold text-ink">{detail.title}</p>{detail.lines.map((line, index) => <p key={index} className="text-muted">{line}</p>)}{detail.connections && <CorrelationEvidence connections={detail.connections} />}{detail.entityId && <GraphEvidence entity={entity} loading={entityLoading} />}{detail.href && <SourcePreview href={detail.href} label={detail.hrefLabel ?? "Source"} onEmbed={onEmbed && canPreviewSource(detail.href) ? () => onEmbed(detail) : undefined} />}</div> : <p className="text-xs text-muted">Select an alert or map element to inspect its operational context.</p>}</div></Panel>;
}

function CollapsedSidebarTabs({ onOpen }: { onOpen: () => void }) {
  return <div className="flex shrink-0 gap-1"><button aria-label="Show areas of interest" title="Show areas of interest" onClick={onOpen} className="grid h-8 w-8 place-items-center border border-line bg-panel text-muted hover:border-command/60 hover:text-command"><Crosshair size={14} /></button><button aria-label="Show AI assessments" title="Show AI assessments" onClick={onOpen} className="grid h-8 w-8 place-items-center border border-line bg-panel text-muted hover:border-command/60 hover:text-command"><AlertTriangle size={14} /></button></div>;
}

function telegramPostId(href: string) {
  try {
    const parts = new URL(href).pathname.split("/").filter(Boolean);
    const [channel, post] = parts[0] === "s" ? parts.slice(1) : parts;
    return channel && /^\d+$/.test(post ?? "") ? `${channel}/${post}` : undefined;
  } catch { return undefined; }
}

function canPreviewSource(href?: string) {
  if (!href) return false;
  try {
    return new URL(href).hostname !== "t.me" || Boolean(telegramPostId(href));
  } catch { return false; }
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

function CandidateQueue({ candidates }: { candidates: FusionCandidate[] }) {
  return <Panel className="max-h-36 shrink-0 overflow-auto scrollbar"><div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-panel px-3 py-2"><div><h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Cross-source candidates</h2><p className="text-[9px] text-muted">Pair-specific retrieval, awaiting adjudication</p></div><Badge>{candidates.length}</Badge></div>{candidates.slice(0, 40).map((candidate) => <div key={candidate.id} className="grid grid-cols-[1fr_auto] border-b border-line/70 px-3 py-1.5"><div className="min-w-0"><p className="truncate font-mono text-[9px] uppercase text-ink">{candidate.left_kind} ↔ {candidate.right_kind}</p><p className="truncate text-[9px] text-muted" title={`${candidate.left_id} ↔ ${candidate.right_id}`}>{candidate.left_id} ↔ {candidate.right_id}</p></div><div className="pl-2 text-right font-mono text-[9px] text-command">{candidate.candidate_score.toFixed(2)}<p className="text-muted">{candidate.distance_km} km · {candidate.dt_min} m</p></div></div>)}{candidates.length === 0 && <p className="px-3 py-4 text-[10px] text-muted">No cross-source candidates in the current picture.</p>}</Panel>;
}

function AssessmentQueue({ assessments, total, clusters, showRejected, onToggleRejected }: { total: number; assessments: Assessment[]; clusters: FusionCluster[]; showRejected: boolean; onToggleRejected: () => void }) {
  return <Panel className="max-h-52 shrink-0 overflow-auto scrollbar">
    <div className="sticky top-0 z-10 flex items-center border-b border-line bg-panel px-3 py-2">
      <div><h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Evidence assessments</h2><p className="text-[9px] text-muted">Article identity + incident evidence</p></div>
      <label className="ml-auto flex items-center gap-1 text-[9px] text-muted"><input type="checkbox" checked={showRejected} onChange={onToggleRejected} /> Show rejected</label>
    </div>
    {clusters[0]?.brief && <div className="border-b border-command/30 bg-command/5 px-3 py-2">
      <p className="font-mono text-[9px] uppercase text-command">Top cluster · {clusters[0].modalities.join(" + ")}</p>
      <p className="mt-1 text-[10px] text-muted">{clusters[0].brief}</p>
      {clusters[0].caveats.map((caveat, i) => <p key={i} className="mt-1 text-[9px] text-muted">{caveat}</p>)}
    </div>}
    {assessments.map((assessment) => <div key={assessment.id} className="border-b border-line/70 px-3 py-2"><AssessmentResult assessment={assessment} compact /></div>)}
    {assessments.length === 0 && <p className="px-3 py-4 text-[10px] text-muted">{total ? `${total} pairs assessed. Enable Show rejected to inspect results without a supported link.` : "No assessments yet; check the OpenAI source status."}</p>}
  </Panel>;
}

function AICompareOverlay({ status, active, selections, result, pending, error, onToggle, onAnalyze, onClear }: { status?: AIStatus; active: boolean; selections: Detail[]; result: Assessment | null; pending: boolean; error?: Error | null; onToggle: () => void; onAnalyze: () => void; onClear: () => void }) {
  return <div className="absolute left-3 top-3 z-[1000] max-h-[90%] w-80 overflow-auto border border-line bg-panel/95 p-2 shadow-panel">
    <div className="flex items-center gap-2">
      <Button size="sm" variant={active ? "critical" : "outline"} onClick={onToggle}><Link2 size={12} /> {active ? "Selecting evidence" : "Compare with AI"}</Button>
      <span className={status?.configured ? "font-mono text-[9px] text-command" : "font-mono text-[9px] text-[#eab85a]"}>{status?.configured ? status.model : "KEY NOT SET"}</span>
      {selections.length > 0 && <button className="ml-auto text-[9px] text-muted hover:text-ink" onClick={onClear}>Clear</button>}
    </div>
    {active && <>
      <p className="mt-2 text-[10px] text-muted">Select two individual AIS, GDELT, Telegram, ADS-B, or FIRMS markers.</p>
      <div className="mt-2 grid grid-cols-2 gap-1">{[0, 1].map((index) => <div key={index} className="min-h-12 border border-line bg-black/15 p-1.5">
        <p className="font-mono text-[8px] uppercase text-command">{index ? "B" : "A"}</p><p className="truncate text-[10px] text-ink">{selections[index]?.title ?? "Select marker"}</p>
      </div>)}</div>
      <Button className="mt-2 w-full" size="sm" disabled={selections.length !== 2 || pending || !status?.configured} onClick={onAnalyze}>
        {pending ? "Fetching context / evaluating…" : result ? "Reanalyze with fresh source text" : "Adjudicate evidence"}
      </Button>
    </>}
    {error && <p className="mt-2 text-[10px] text-critical">{error.message}</p>}
    {result && <div className="mt-2 border-t border-line pt-2"><AssessmentResult assessment={result} /></div>}
  </div>;
}

/** One AOI row: click the name to rename it in place (Enter saves, Esc cancels, blur saves), double-click to zoom the map to it. */
function RegionRow({ region, onRename, onRemove, onZoom }: { region: Region; onRename: (id: string, name: string) => void; onRemove: (id: string) => void; onZoom: (region: Region) => void }) {
  const [editing, setEditing] = useState(false); const [name, setName] = useState(region.name); const settled = useRef(false);
  const commit = (save: boolean) => { if (settled.current) return; settled.current = true; setEditing(false); const next = name.trim(); if (save && next && next !== region.name) onRename(region.id, next); };
  const start = () => { settled.current = false; setName(region.name); setEditing(true); };
  return <tr className="border-t border-line/70"><td className="py-1.5 text-ink" title="Click to rename · double-click to zoom" onDoubleClick={() => onZoom(region)}>{editing ? <input autoFocus aria-label={`Rename ${region.name}`} value={name} onChange={(event) => setName(event.target.value)} onBlur={() => commit(true)} onKeyDown={(event) => { if (event.key === "Enter") commit(true); if (event.key === "Escape") commit(false); }} className="h-6 w-full border border-line bg-canvas px-1 text-[11px] text-ink outline-none focus:border-command" /> : <button type="button" onClick={start} className={`cursor-text text-left hover:text-command ${region.user ? "text-[#94c973]" : "text-ink"}`}>{region.name}</button>}</td><td className="py-1.5 font-mono text-muted">{region.radius_nm} nm</td><td className="py-1.5 text-right"><button aria-label={`Remove ${region.name}`} onClick={() => onRemove(region.id)} className="text-muted hover:text-critical"><Trash2 size={13} /></button></td></tr>;
}

function Regions({ regions, filterAoi, drawing, draft, onToggleFilter, onToggleDrawing, onNameChange, onSave, onCancel, onRemove, onRename, onZoom }: { regions: Region[]; filterAoi: boolean; drawing: boolean; draft: Omit<Region, "id"> | null; onToggleFilter: () => void; onToggleDrawing: () => void; onNameChange: (value: string) => void; onSave: () => void; onCancel: () => void; onRemove: (id: string) => void; onRename: (id: string, name: string) => void; onZoom: (region: Region) => void }) {
  return <Panel className="p-3"><div className="flex items-center gap-2"><h2 className="mr-auto font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Areas of interest</h2><Button size="sm" variant={drawing ? "critical" : "outline"} onClick={onToggleDrawing}>{drawing ? "Cancel" : <><Crosshair size={13} /> Draw</>}</Button></div><p className="mt-1 text-[10px] leading-snug text-muted">A circle adds a live <b className="text-ink">aircraft</b> query (adsb.lol, 60 s) and an <b className="text-ink">AIS vessel</b> stream (AOI circles only), and a <b className="text-ink">thermal-anomaly</b> query (NASA FIRMS, 15 min) for that area; the military feed, GDELT and Telegram are global already. Click a name to rename, double-click to zoom.</p><label className="mt-2 flex cursor-pointer items-center gap-2 text-[11px] text-muted"><input type="checkbox" checked={filterAoi} onChange={onToggleFilter} /> Filter view to AOIs</label>{drawing && <p className="mt-2 border-l-2 border-command pl-2 text-[11px] text-command">Press and drag on the map to define a 5–250 nm circle.</p>}{draft && <div className="mt-3 space-y-2 border border-command/50 bg-command/5 p-2"><p className="font-mono text-[10px] uppercase text-command">New AOI · {draft.radius_nm} nm</p><input autoFocus value={draft.name} onChange={(event) => onNameChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onSave(); if (event.key === "Escape") onCancel(); }} className="h-8 w-full border border-line bg-canvas px-2 text-xs text-ink outline-none focus:border-command" /><div className="flex gap-2"><Button size="sm" onClick={onSave}>Save</Button><Button size="sm" variant="ghost" onClick={onCancel}>Discard</Button></div></div>}<div className="mt-3 max-h-28 overflow-auto scrollbar"><table className="w-full text-left text-[11px]"><tbody>{regions.map((region) => <RegionRow key={region.id} region={region} onRename={onRename} onRemove={onRemove} onZoom={onZoom} />)}</tbody></table></div></Panel>;
}

export function App() {
  const client = useQueryClient();
  const selectingMapItem = useRef(false);
  const [mode, setMode] = useState("live"); const [viewport, setViewport] = useState(WORLD); const view = useDebounced(viewport, 250); const max = limits(view.zoom); const key = viewportKey(view);
  const [layers, setLayers] = useState<LayerState>({ events: true, tracks: true, firms: true, links: true, imagery: false, rf: true, ais: true }); const [timelineHours, setTimelineHours] = useState(24); const [sourcesOpen, setSourcesOpen] = useState(false);
  const [replayTime, setReplayTime] = useState<number | null>(null); const [playing, setPlaying] = useState(false); const [speed, setSpeed] = useState(300); const [filterAoi, setFilterAoi] = useState(false); const [drawing, setDrawing] = useState(false); const [draft, setDraft] = useState<Omit<Region, "id"> | null>(null); const [detail, setDetail] = useState<Detail | null>(null); const [sourceViewer, setSourceViewer] = useState<Detail | null>(null); const [focus, setFocus] = useState<[number, number, number] | undefined>();
  const [compareActive, setCompareActive] = useState(false); const [compareSelection, setCompareSelection] = useState<Detail[]>([]); const [compareResult, setCompareResult] = useState<Assessment | null>(null); const [showRejected, setShowRejected] = useState(false);
  const compareContext = useRef({ mode, selections: compareSelection });
  compareContext.current = { mode, selections: compareSelection };
  const live = mode === "live"; const polling = 60_000; const trackPolling = 10_000;
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, enabled: live, refetchInterval: live ? polling : false });
  const events = useQuery({ queryKey: ["events", key, max.events], queryFn: () => api.events(view, max.events), enabled: live && layers.events, refetchInterval: live && layers.events ? polling : false, placeholderData: keepPreviousData });
  const tracks = useQuery({ queryKey: ["tracks", key, max.tracks], queryFn: () => api.tracks(view, max.tracks), enabled: live && layers.tracks, refetchInterval: live && layers.tracks ? (query) => (Array.isArray(query.state.data) && query.state.data.length > 0 ? polling : trackPolling) : false, placeholderData: keepPreviousData });
  const tails = useQuery({ queryKey: ["tails", key, max.tracks], queryFn: () => api.tails(view, max.tracks), enabled: live && layers.tracks, refetchInterval: live && layers.tracks ? polling : false, placeholderData: keepPreviousData });
  const firms = useQuery({ queryKey: ["firms", key, max.firms], queryFn: () => api.firms(view, max.firms), enabled: live && layers.firms, refetchInterval: live && layers.firms ? polling : false, placeholderData: keepPreviousData });
  const ais = useQuery({ queryKey: ["ais"], queryFn: api.ais, enabled: live && layers.ais, refetchInterval: live && layers.ais ? 10_000 : false });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, enabled: live, refetchInterval: live ? polling : false, placeholderData: keepPreviousData });
  const aiStatus = useQuery({ queryKey: ["fusion-ai-status"], queryFn: api.aiStatus, refetchInterval: polling });
  const aiCandidates = useQuery({ queryKey: ["fusion-candidates"], queryFn: api.candidates, enabled: live, refetchInterval: live ? polling : false, placeholderData: keepPreviousData });
  const aiAssessments = useQuery({ queryKey: ["fusion-assessments"], queryFn: () => api.assessments(true), enabled: live, refetchInterval: live ? 5_000 : false, placeholderData: keepPreviousData });
  const aiClusters = useQuery({ queryKey: ["fusion-clusters"], queryFn: api.clusters, enabled: live, refetchInterval: live ? polling : false, placeholderData: keepPreviousData });
  const liveTimeline = useQuery({ queryKey: ["timeline", timelineHours], queryFn: () => api.timeline(timelineHours), enabled: live, refetchInterval: live ? polling : false, placeholderData: keepPreviousData });
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios }); const config = useQuery({ queryKey: ["replay-config", mode], queryFn: () => api.replayConfig(mode), enabled: !live }); const snapshot = useQuery({ queryKey: ["replay", mode, replayTime], queryFn: () => api.replayAt(mode, replayTime!), enabled: !live && replayTime !== null, placeholderData: keepPreviousData }); const replayTimeline = useQuery({ queryKey: ["replay-timeline", mode], queryFn: () => api.replayTimeline(mode), enabled: !live, staleTime: Infinity }); const regions = useQuery({ queryKey: ["regions"], queryFn: api.regions }); const entity = useQuery({ queryKey: ["entity", detail?.entityId], queryFn: () => api.entity(detail!.entityId!), enabled: live && Boolean(detail?.entityId), retry: false });
  useEffect(() => { client.invalidateQueries({ queryKey: ["ais"] }); }, [regions.data, client]);
  const addRegion = useMutation({ mutationFn: api.addRegion, onSuccess: () => { client.invalidateQueries({ queryKey: ["regions"] }); setDraft(null); setDrawing(false); } }); const removeRegion = useMutation({ mutationFn: api.removeRegion, onSuccess: () => client.invalidateQueries({ queryKey: ["regions"] }) }); const renameRegion = useMutation({ mutationFn: ({ id, name }: { id: string; name: string }) => api.renameRegion(id, name), onSuccess: () => client.invalidateQueries({ queryKey: ["regions"] }) }); const refresh = useMutation({ mutationFn: api.refresh, onSuccess: () => ["status", "events", "tracks", "firms", "alerts", "timeline"].forEach((queryKey) => client.invalidateQueries({ queryKey: [queryKey] })) });
  const adjudicate = useMutation({ mutationFn: (request: AdjudicationRequest) => api.adjudicate(request.left, request.right, request.mode, request.time, request.force), onSuccess: (value, request) => { const current = compareContext.current; if (current.mode === request.mode && current.selections.length === 2 && [request.left, request.right].every((ref, index) => current.selections[index].recordRef?.kind === ref.kind && current.selections[index].recordRef?.id === ref.id)) setCompareResult(value); client.invalidateQueries({ queryKey: ["fusion-candidates"] }); client.invalidateQueries({ queryKey: ["fusion-assessments"] }); client.invalidateQueries({ queryKey: ["fusion-clusters"] }); client.invalidateQueries({ queryKey: ["graph"] }); if (request.mode !== "live") client.invalidateQueries({ queryKey: ["replay", request.mode, request.time] }); } });
  const zoomToRegion = useCallback((region: Region) => {
    // This maps the circle diameter to a useful Leaflet zoom level.  A new
    // tuple on every click makes the already-proven FocusMap effect run again.
    const lat = Number(region.lat), lon = Number(region.lon), radiusNm = Number(region.radius_nm);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(radiusNm) || radiusNm <= 0) return;
    const zoom = Math.max(3, Math.min(16, Math.round(Math.log2(30000 / (radiusNm * 1.852)))));
    setFocus([lat, lon, zoom]);
  }, []);
  useEffect(() => { if (config.data && replayTime === null) { setReplayTime(Math.min(config.data.t_max, config.data.t_min + 43_200)); setFocus([config.data.scenario.center[0], config.data.scenario.center[1], config.data.scenario.zoom]); } }, [config.data, replayTime]);
  useEffect(() => { if (!playing || !config.data) return; const timer = window.setInterval(() => setReplayTime((time) => { const next = Math.min(config.data!.t_max, (time ?? config.data!.t_min) + speed); if (next >= config.data!.t_max) setPlaying(false); return next; }), 1000); return () => window.clearInterval(timer); }, [playing, speed, config.data]);
  const replay = snapshot.data ?? EMPTY_REPLAY;
  const records = live ? { live: true, vessels: ais.data?.vessels ?? [], events: events.data ?? [], tracks: tracks.data ?? [], alerts: alerts.data ?? [], firms: firms.data ?? [], tails: tails.data ?? [] } : { live: false, vessels: [], events: replay.events, tracks: replay.tracks, alerts: replay.alerts, firms: replay.firms ?? [], tails: replay.tails ?? [] };
  const currentStatus: Status = live ? status.data ?? EMPTY_STATUS : { counts: replay.counts, updated: replay.t_iso, store: "replay" };
  const allAssessments = live ? aiAssessments.data ?? [] : replay.assessments ?? [];
  const assessed = allAssessments.filter((value) =>
    showRejected || value.verdict === "SUPPORTED" || value.verdict === "PLAUSIBLE" || value.has_article_match);
  const candidates = live ? aiCandidates.data ?? [] : replay.candidates ?? [];
  const clusters = live ? aiClusters.data ?? [] : replay.clusters ?? [];
  const shownGdelt = records.events.filter((event) => !event.id.startsWith("tg:")).length;
  const shownTelegram = records.events.filter((event) => event.id.startsWith("tg:")).length;

  const selectMap = useCallback((next: Detail) => {
    // Marker clicks may bubble to Leaflet's map click handler.  Hold a flag
    // through that event turn so selecting a marker cannot immediately clear it.
    selectingMapItem.current = true;
    if (compareActive && next.recordRef) {
      // `keepPreviousData` intentionally leaves the prior snapshot on screen while a new replay
      // instant loads. Bind evidence to the timestamp that actually rendered this marker, not the
      // newer scrubber value, and stop playback so the second selection comes from the same instant.
      const selectedTime = live ? undefined : replay.t;
      const selected = selectedTime === undefined ? next : { ...next, replayTime: selectedTime };
      if (selectedTime !== undefined) {
        setPlaying(false);
        setReplayTime(selectedTime);
      }
      setCompareSelection((current) => {
        if (current.some((item) => item.recordRef?.kind === selected.recordRef?.kind && item.recordRef?.id === selected.recordRef?.id)) return current;
        if (current.length > 0 && current[0].replayTime !== selected.replayTime) return [selected];
        return current.length >= 2 ? [selected] : [...current, selected];
      });
      setCompareResult(null);
    }
    setDetail(next); setSourceViewer(null);
    window.setTimeout(() => { selectingMapItem.current = false; }, 0);
  }, [compareActive, live, replay.t]);
  const clearMapSelection = useCallback(() => {
    if (!selectingMapItem.current) { setDetail(null); setSourceViewer(null); }
  }, []);
  const missingAircraftDays = (config.data?.days ?? []).filter((d) => !(config.data?.layers_loaded?.adsb ?? []).includes(d));

  return <main className="relative z-10 min-h-screen p-2 text-ink lg:h-screen lg:overflow-hidden"><SourcesDialog open={sourcesOpen} onClose={() => setSourcesOpen(false)} /><div className="grid min-h-[calc(100vh-1rem)] grid-rows-[auto_auto_1fr] overflow-hidden border border-line bg-canvas/95 lg:h-[calc(100vh-1rem)]">
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-4 py-3"><div className="mr-2 flex items-center gap-2"><ScanSearch size={18} className="text-command" /><div><h1 className="font-mono text-sm font-bold tracking-[.12em]">MULTI-INT FUSION</h1><p className="font-mono text-[9px] tracking-[.16em] text-muted">COMMAND CONSOLE</p></div></div><Metric label="OSINT ITEMS" value={currentStatus.counts.events.toLocaleString()} detail={`${currentStatus.counts.conflict_events.toLocaleString()} conflict-coded`} color="text-command" help="Current GDELT events plus geolocated Telegram posts. Conflict-coded items are the subset classified by GDELT as protest, force posture, coercion, assault, fighting, or mass violence." /><Metric label="AIRCRAFT" value={currentStatus.counts.tracks.toLocaleString()} detail={`${currentStatus.counts.military_tracks.toLocaleString()} military`} color="text-[#5cc7da]" help="Aircraft in the latest ADS-B snapshot. Military aircraft are a subset of that total." /><Metric label="CANDIDATES" value={currentStatus.counts.candidates ?? currentStatus.counts.alerts} detail="retrieval only" color="text-[#eab85a]" /><Metric label="AI ASSESSMENTS" value={allAssessments.length} detail={`${assessed.filter((value) => value.verdict === "PLAUSIBLE").length} need review`} color="text-command" /><Metric label="UPDATED" value={formatTime(currentStatus.updated)} /><div className="ml-auto flex flex-wrap items-center gap-2"><Button size="sm" variant="outline" onClick={() => setSourcesOpen(true)}><Info size={13} /> Sources</Button><select value={mode} onChange={(event) => { setDetail(null); setDrawing(false); setDraft(null); setPlaying(false); setCompareSelection([]); setCompareResult(null); setMode(event.target.value); setReplayTime(null); if (event.target.value === "live") setFocus([35, 10, 2]); }} className="h-9 border border-line bg-canvas px-2 font-mono text-[11px] text-ink"><option value="live">LIVE · GLOBAL STREAM</option>{(scenarios.data ?? []).map((scenario) => <option key={scenario.id} value={scenario.id}>REPLAY · {scenario.title}</option>)}</select>{live && <Button disabled={refresh.isPending} title="Re-poll ADS-B for every drawn circle and re-fuse; can take a few minutes under adsb.lol rate limits" onClick={() => refresh.mutate()}><RefreshCw size={14} className={refresh.isPending ? "animate-spin" : ""} /> {refresh.isPending ? "Refreshing…" : "Refresh"}</Button>}{live && refresh.isError && <span className="font-mono text-[10px] text-critical" role="alert">Refresh failed: {(refresh.error as Error).message}</span>}</div></header>
    {live ? <div className="border-b border-line bg-black/15 py-2 font-mono text-[10px] text-muted"><div className="flex items-center gap-3 px-4"><span>GDELT window {formatWindow(currentStatus.gdelt_window)}</span><span className="hidden xl:inline">{max.events} OSINT / {max.tracks} ADS-B cap</span><select value={timelineHours} onChange={(event) => setTimelineHours(Number(event.target.value))} className="ml-auto border border-line bg-canvas px-1 text-[10px] text-ink"><option value={1}>1 hour</option><option value={6}>6 hours</option><option value={24}>24 hours</option><option value={0}>All history</option></select></div><div className="flex flex-wrap gap-y-2 px-1 pt-2"><SourceSignal label="GDELT · OSINT" markerColor="#eab85a" markerLabel="Yellow OSINT event" source={currentStatus.sources?.gdelt} shown={shownGdelt} isFetching={events.isFetching || status.isFetching} requestError={events.isError} enabled={layers.events} fallback="Geocoded news and event records" /><SourceSignal label="ADS-B · AIRCRAFT" markerColor="#5cc7da" markerLabel="Blue aircraft" source={currentStatus.sources?.adsb} shown={records.tracks.length} isFetching={tracks.isFetching || status.isFetching} requestError={tracks.isError} enabled={layers.tracks} fallback="Live aircraft positions" /><SourceSignal label="AIS · VESSELS" markerColor="#34d399" markerLabel="Green AIS vessel" source={ais.data?.source} shown={records.vessels.length} isFetching={ais.isFetching} requestError={ais.isError} enabled={layers.ais} fallback="Live vessels inside AOIs only" /><SourceSignal label="FIRMS · THERMAL" markerColor="#f87171" markerLabel="Red thermal anomaly" source={currentStatus.sources?.firms} shown={records.firms.length} isFetching={firms.isFetching || status.isFetching} requestError={firms.isError} enabled={layers.firms} fallback="NASA VIIRS thermal anomalies" /><SourceSignal label="TELEGRAM · OSINT" markerColor="#e879f9" markerLabel="Magenta Telegram post" source={currentStatus.sources?.telegram} shown={shownTelegram} isFetching={status.isFetching} fallback="Public geolocated channel previews" /><SourceSignal label="FUSION · CANDIDATES" markerColor="#eab85a" markerLabel="Dashed candidate link" source={currentStatus.sources?.fusion} shown={candidates.length} isFetching={aiCandidates.isFetching || status.isFetching} requestError={aiCandidates.isError} fallback="Spatial, temporal, and source-entity candidate retrieval" /><SourceSignal label="OPENAI · ADJUDICATION" markerColor="#94c973" markerLabel="AI assessed finding" source={currentStatus.sources?.openai} shown={allAssessments.length} isFetching={aiAssessments.isFetching} requestError={aiAssessments.isError} fallback="Structured evidence plus on-demand GDELT source text" /><MapKey label="AOI · WATCH AREA" color="#a78bfa" detail="Purple boundary and center dot. Click the center dot to zoom to this watch area." /></div></div> : <div className="flex flex-wrap items-center gap-3 border-b border-line bg-black/15 px-4 py-2"><Badge className="border-command/40 text-command">Replay</Badge><span className="max-w-md truncate text-xs text-muted">{config.data?.scenario.notes ?? "Loading scenario…"}</span><Button size="sm" variant="outline" onClick={() => setPlaying((value) => !value)}>{playing ? <Pause size={13} /> : <Play size={13} />}{playing ? "Pause" : "Play"}</Button><select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} className="h-7 border border-line bg-canvas px-1 font-mono text-[10px] text-ink"><option value={60}>1 min/s</option><option value={300}>5 min/s</option><option value={900}>15 min/s</option></select><input className="min-w-32 flex-1 accent-[hsl(var(--command))]" type="range" min={config.data?.t_min ?? 0} max={config.data?.t_max ?? 1} step={60} value={replayTime ?? 0} onChange={(event) => { setCompareSelection([]); setCompareResult(null); setReplayTime(Number(event.target.value)); }} /><span className="font-mono text-[11px] text-ink">{replay.t_iso ? `${replay.t_iso.slice(0, 16).replace("T", " ")} UTC` : "Loading…"}</span><span className="text-[10px] text-muted">{missingAircraftDays.length > 0 && `Aircraft missing for ${missingAircraftDays.join(", ")}`}</span></div>}
    {/* Desktop grid: map (col 1, row 1) with the timeline full-width beneath it (col 1, row 2); the side panels take the whole right column so the alert queue keeps its height. */}
    <div className="grid min-h-0 gap-2 p-2 lg:grid-cols-[minmax(0,1fr)_390px] lg:grid-rows-[minmax(0,1fr)_240px]">
      <Panel className="relative min-h-[440px] overflow-hidden lg:min-h-0"><OperationalMap {...records} assessments={assessed} regions={regions.data ?? []} layers={layers} viewport={viewport} filterAoi={filterAoi} drawing={drawing} focus={focus} satelliteDay={live ? undefined : (replayTime ? new Date(replayTime * 1000).toISOString().slice(0, 10) : config.data?.scenario.day)} replayBounds={live ? undefined : config.data?.scenario.bbox} compareSelection={compareSelection} compareVerdict={compareResult?.verdict} compareArticleMatch={compareResult?.has_article_match} onViewport={setViewport} onLayerToggle={(layer) => setLayers((value) => ({ ...value, [layer]: !value[layer] }))} onDraft={(value) => { setDraft(value); setDrawing(false); }} onSelect={selectMap} onAoiZoom={zoomToRegion} onBackgroundClick={clearMapSelection} /><AICompareOverlay status={aiStatus.data} active={compareActive} selections={compareSelection} result={compareResult} pending={adjudicate.isPending} error={adjudicate.error as Error | null} onToggle={() => { setCompareActive((value) => !value); setCompareSelection([]); setCompareResult(null); }} onAnalyze={() => { const [left, right] = compareSelection; if (!left?.recordRef || !right?.recordRef) return; setPlaying(false); adjudicate.mutate({ left: left.recordRef, right: right.recordRef, mode, time: live ? null : left.replayTime ?? replay.t, force: Boolean(compareResult) }); }} onClear={() => { setCompareSelection([]); setCompareResult(null); }} /><div className="pointer-events-none absolute bottom-3 left-3 border border-line bg-panel/95 px-2 py-1 font-mono text-[10px] uppercase tracking-wide text-muted">Canvas map · clusters expand on click</div></Panel>
      <aside className="relative flex min-h-0 flex-col gap-2 lg:row-span-2">
        {detail ? <><Inspector detail={detail} entity={entity.data} entityLoading={entity.isLoading} expanded onClose={() => { setDetail(null); setSourceViewer(null); }} onEmbed={(d) => setSourceViewer(d)} /><CollapsedSidebarTabs onOpen={() => { setDetail(null); setSourceViewer(null); }} /></> : <>
          <Regions regions={regions.data ?? []} filterAoi={filterAoi} drawing={drawing} draft={draft} onToggleFilter={() => setFilterAoi((value) => !value)} onToggleDrawing={() => { setDrawing((value) => !value); setDraft(null); }} onNameChange={(name) => setDraft((value) => value ? { ...value, name } : value)} onSave={() => { if (draft) addRegion.mutate({ lat: draft.lat, lon: draft.lon, radius_nm: draft.radius_nm, name: draft.name }); }} onCancel={() => setDraft(null)} onRemove={(id) => removeRegion.mutate(id)} onRename={(id, name) => renameRegion.mutate({ id, name })} onZoom={zoomToRegion} />
          <CandidateQueue candidates={candidates} />
          <AssessmentQueue assessments={assessed} total={allAssessments.length} clusters={clusters} showRejected={showRejected} onToggleRejected={() => setShowRejected((value) => !value)} />
          <Inspector detail={null} onEmbed={(d) => setSourceViewer(d)} />
          {!live && mode && <CuratedEvidence scenarioId={mode} tMin={config.data?.t_min} tMax={config.data?.t_max} onSeek={(t) => { setPlaying(false); setReplayTime(t); }} />}
        </>}
        {sourceViewer && <SourceViewer detail={sourceViewer} onClose={() => setSourceViewer(null)} />}
      </aside>
      <Panel className="relative min-h-[240px] overflow-hidden lg:min-h-0"><ActivityTimeline timeline={live ? liveTimeline.data : replayTimeline.data} activeTime={live ? undefined : replayTime} mode={live ? "live" : "replay"} onSeek={live ? undefined : (time) => { setPlaying(false); setReplayTime(time); }} /></Panel>
    </div>
  </div></main>;
}
