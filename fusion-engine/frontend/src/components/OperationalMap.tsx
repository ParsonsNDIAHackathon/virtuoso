import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { RfSpectrumDialog } from "./RfSpectrumDialog";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, Rectangle, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { Alert, Assessment, Event, Firms, Incident, RecordRef, Region, Sar, Tail, Track, Viewport } from "../lib/types";
import { distanceKm } from "../lib/utils";

const NM_KM = 1.852;
const renderer = L.canvas({ padding: .5 });
const eventColor = "#eab85a";
const aoiColor = "#a78bfa";
const LAYER_LABELS: Record<keyof LayerState, string> = { events: "OSINT", tracks: "ADS-B", firms: "Thermal", sar: "SAR", links: "Links", imagery: "Imagery", rf: "RF", incidents: "Incidents" };
const clusterIcon = (count: number, color: string) => L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17], html: `<span style="display:grid;place-items:center;width:34px;height:34px;border-radius:50%;border:2px solid ${color};background:#101710e8;color:${color};font:600 10px monospace">${count > 999 ? "999+" : count}</span>` });
const rfIcon = L.divIcon({
  className: "", iconSize: [32, 32], iconAnchor: [16, 16],
  html: '<svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true"><circle cx="16" cy="16" r="15" fill="#101710" stroke="#eab85a"/><path d="M18 5 8 18h7l-1 9 10-14h-7z" fill="#eab85a"/></svg>',
});
const planeIcons = new Map<string, L.DivIcon>();
const thermalIcons = new Map<string, L.DivIcon>();
// Keep the AOI target as a DOM marker, rather than a canvas shape.  Leaflet
// reliably dispatches marker clicks even when the shared canvas renderer has
// many data layers above or below the AOI circle.
const aoiCenterIcon = L.divIcon({
  className: "", iconSize: [20, 20], iconAnchor: [10, 10],
  html: '<span title="Zoom to this watch area" style="display:block;box-sizing:border-box;width:18px;height:18px;border:3px solid #101710;border-radius:50%;background:#a78bfa;box-shadow:0 0 0 1px #c4b5fd,0 1px 4px #101710;cursor:zoom-in" aria-label="Zoom to this watch area"></span>',
});
const telegramIcon = L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: '<svg viewBox="0 0 32 32" width="22" height="22" style="display:block;filter:drop-shadow(0 0 2px #101710)" aria-label="Telegram post"><circle cx="16" cy="16" r="14" fill="#2387c6" stroke="#101710" stroke-width="1.5"/><path d="m7 15.1 17-6.7c.8-.3 1.5.2 1.2 1.2l-3.1 14.1c-.2 1-1 1.2-1.8.7l-4.6-3.4-2.2 2.1c-.2.2-.4.4-.9.4l.3-4.8 8.8-8c.4-.4-.1-.6-.6-.3L10.2 17l-4.7-1.5c-1-.3-1-1 .2-1.4z" fill="#effaff"/></svg>',
});
const osintIcon = L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: '<svg viewBox="0 0 32 32" width="22" height="22" style="display:block;filter:drop-shadow(0 0 2px #101710)" aria-label="OSINT news report"><circle cx="16" cy="16" r="14" fill="#eab85a" stroke="#101710" stroke-width="1.5"/><rect x="9" y="8.5" width="14" height="15" rx="1" fill="#fff8e7"/><rect x="11" y="11" width="4" height="5" rx=".5" fill="#d69d3c"/><path d="M17 11h4M17 14h4M11 18h10M11 21h8" stroke="#694713" stroke-width="1.4" stroke-linecap="round"/></svg>',
});
const redditIcon = L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: '<svg viewBox="0 0 32 32" width="22" height="22" style="display:block;filter:drop-shadow(0 0 2px #101710)" aria-label="Reddit post"><circle cx="16" cy="16" r="14" fill="#ff4500" stroke="#101710" stroke-width="1.5"/><circle cx="11" cy="15" r="2.2" fill="#101710"/><circle cx="21" cy="15" r="2.2" fill="#101710"/><path d="M9 20c2 2.4 12 2.4 14 0" stroke="#101710" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>',
});
const blueskyIcon = L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: '<svg viewBox="0 0 32 32" width="22" height="22" style="display:block;filter:drop-shadow(0 0 2px #101710)" aria-label="Bluesky post"><circle cx="16" cy="16" r="14" fill="#1185fe" stroke="#101710" stroke-width="1.5"/><path d="M9 12.5c1.2 1 4.2 3.6 7 3.6s5.8-2.6 7-3.6c-.6 3.2-1.6 7.6-2.6 9.2-.9 1.4-2.6 1.7-4.4 1.7s-3.5-.3-4.4-1.7c-1-1.6-2-6-2.6-9.2z" fill="#effaff"/></svg>',
});
const mastodonIcon = L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: '<svg viewBox="0 0 32 32" width="22" height="22" style="display:block;filter:drop-shadow(0 0 2px #101710)" aria-label="Mastodon post"><circle cx="16" cy="16" r="14" fill="#6364ff" stroke="#101710" stroke-width="1.5"/><circle cx="12" cy="14" r="2" fill="#effaff"/><circle cx="20" cy="14" r="2" fill="#effaff"/><path d="M11 20c1.4 1.6 8.6 1.6 10 0" stroke="#effaff" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>',
});

// GDELT can cite a bare social landing page (t.me channel page, reddit thread
// index, sometimes a stale username). Only records created by the social
// ingesters are actual, individual posts — identified by their id prefix.
function socialPlatform(event: Event): string | null {
  const id = event.id ?? "";
  if (id.startsWith("tg:")) return "telegram";
  if (id.startsWith("reddit:")) return "reddit";
  if (id.startsWith("bsky:")) return "bluesky";
  if (id.startsWith("mastodon:") || id.startsWith("md:")) return "mastodon";
  return null;
}

function socialIcon(event: Event) {
  switch (socialPlatform(event)) {
    case "telegram": return telegramIcon;
    case "reddit": return redditIcon;
    case "bluesky": return blueskyIcon;
    case "mastodon": return mastodonIcon;
    default: return undefined;
  }
}

function socialTitle(event: Event) {
  switch (socialPlatform(event)) {
    case "telegram": return `Telegram · ${event.source_domain?.replace(/^t\.me\//, "") || "post"}`;
    case "reddit": return `Reddit · ${event.source_domain?.replace(/^reddit\.com\//, "") || "post"}`;
    case "bluesky": return "Bluesky · post";
    case "mastodon": return `Mastodon · ${event.source_domain || "post"}`;
    default: return event.root_label;
  }
}

function isSocial(event: Event) { return socialPlatform(event) !== null; }

// Bare social landing pages cited by GDELT (channel pages, thread indexes)
// are not previewable posts — only ingested records link out.
const SOCIAL_LANDING = ["t.me/", "reddit.com/", "bsky.app/", "mastodon"];
function isSocialLanding(url?: string) { return !!url && SOCIAL_LANDING.some((s) => url.includes(s)); }

function thermalIcon(novel: boolean) {
  const key = novel ? "novel" : "routine"; const cached = thermalIcons.get(key); if (cached) return cached;
  const color = novel ? "#f87171" : "#8d2b24";
  const icon = L.divIcon({ className: "", iconSize: [18, 18], iconAnchor: [9, 14], html: `<svg viewBox="0 0 24 24" width="18" height="18" style="display:block;color:${color};filter:drop-shadow(0 0 2px #101710)" aria-label="${novel ? "new" : "routine"} thermal anomaly"><path fill="currentColor" stroke="#101710" stroke-width="1.5" d="M12 2 22 21H2L12 2z"/><path fill="#101710" d="M11 8h2v7h-2zm0 9h2v2h-2z"/></svg>` });
  thermalIcons.set(key, icon); return icon;
}

function aircraftKind(type?: string) {
  const code = (type || "").toUpperCase();
  if (/^(H|EC|UH|MH|CH|AH|KA|R22)|LYNX|HELI/.test(code)) return "helicopter";
  if (/^(F|T|A10|A4)|FIGHTER|HAWK/.test(code)) return "fighter";
  if (/^(C|KC|IL|AN)|CARGO|TANKER/.test(code)) return "heavy";
  if (/^(A3|A2|A1|B7|B8|B9)|AIRBUS|BOEING/.test(code)) return "airliner";
  return "fixed";
}

function planeIcon(track: Track) {
  const heading = Math.round((track.track_deg ?? 0) / 10) * 10;
  const color = track.military ? "#1d4ed8" : "#5cc7da"; const key = `${heading}:${color}`;
  const cached = planeIcons.get(key); if (cached) return cached;
  const icon = L.divIcon({ className: "", iconSize: [24, 24], iconAnchor: [12, 12], html: `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:block;color:${color};filter:drop-shadow(0 0 2px #101710);transform:rotate(${heading}deg);transform-origin:center" aria-label="aircraft"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></svg>` });
  planeIcons.set(key, icon); return icon;
}

export type MapDetail = { title: string; lines: string[]; href?: string; hrefLabel?: string; entityId?: string; recordRef?: RecordRef; point?: [number, number]; connections?: Array<{ kind: "event" | "aircraft"; label: string; detail: string; selected?: boolean }> };
export type LayerState = { events: boolean; tracks: boolean; firms: boolean; sar: boolean; links: boolean; imagery: boolean; rf: boolean; incidents: boolean };
const INCIDENT_COLOR: Record<string, string> = { new_change: "#df5e55", persistent: "#eab85a", recovering: "#5cc7da" };
const STREAM_SHORT: Record<string, string> = { news: "news", conflict: "conflict", social: "social", tracks: "aircraft", military: "military", firms_new: "thermal", navint: "nav integrity" };
type Cluster<T> = { lat: number; lon: number; items: T[] };
type Point = { lat: number; lon: number };
type Props = {
  events: Event[]; tracks: Track[]; alerts: Alert[]; firms: Firms[]; sar: Sar[]; sarCore: Sar[]; tails: Tail[]; regions: Region[]; layers: LayerState; viewport: Viewport;
  assessments?: Assessment[];
  filterAoi: boolean; drawing: boolean; focus?: [number, number, number]; satelliteDay?: string; replayBounds?: [number, number, number, number];
  incidents?: Incident[]; selectedIncident?: string | null; onIncidentSelect?: (id: string) => void;
  compareSelection?: MapDetail[]; compareVerdict?: string; compareArticleMatch?: boolean;
  onDraft: (draft: Omit<Region, "id">) => void; onSelect: (detail: MapDetail) => void; onAoiZoom: (region: Region) => void; onBackgroundClick: () => void; onViewport: (viewport: Viewport) => void; onLayerToggle: (layer: keyof LayerState) => void;
};

function cluster<T extends Point>(items: T[], zoom: number): Cluster<T>[] {
  if (zoom >= 7) return items.map((item) => ({ lat: item.lat, lon: item.lon, items: [item] }));
  const degrees = Math.max(2, 360 / 2 ** zoom * 1.5);
  const groups = new Map<string, Cluster<T>>();
  for (const item of items) {
    const key = `${Math.floor((item.lat + 90) / degrees)}:${Math.floor((item.lon + 180) / degrees)}`;
    const result = groups.get(key) ?? { lat: 0, lon: 0, items: [] };
    result.items.push(item); result.lat += item.lat; result.lon += item.lon; groups.set(key, result);
  }
  return [...groups.values()].map((group) => ({ ...group, lat: group.lat / group.items.length, lon: group.lon / group.items.length }));
}

function FocusMap({ focus }: { focus?: [number, number, number] }) { const map = useMap(); useEffect(() => { if (focus) map.setView([focus[0], focus[1]], focus[2]); }, [focus, map]); return null; }
function MapBackgroundClick({ onClick }: { onClick: () => void }) { useMapEvents({ click: onClick }); return null; }
function ViewportReporter({ onViewport }: { onViewport: Props["onViewport"] }) {
  const map = useMap();
  const report = useCallback(() => { const bounds = map.getBounds(); onViewport({ west: Math.max(-180, bounds.getWest()), south: Math.max(-90, bounds.getSouth()), east: Math.min(180, bounds.getEast()), north: Math.min(90, bounds.getNorth()), zoom: Math.round(map.getZoom()) }); }, [map, onViewport]);
  useMapEvents({ moveend: report, zoomend: report }); useEffect(() => { report(); }, [report]); return null;
}
function AreaDrawer({ enabled, onDraft }: { enabled: boolean; onDraft: Props["onDraft"] }) {
  const map = useMap(); const origin = useRef<L.LatLng | null>(null);
  useEffect(() => { map.getContainer().style.cursor = enabled ? "crosshair" : ""; if (!enabled) map.dragging.enable(); }, [enabled, map]);
  useMapEvents({ mousedown(event) { if (enabled) { origin.current = event.latlng; map.dragging.disable(); } }, mouseup(event) { if (!enabled || !origin.current) return; const center = origin.current; origin.current = null; map.dragging.enable(); const radius_nm = Math.min(250, Math.max(5, Math.round(distanceKm(center.lat, center.lng, event.latlng.lat, event.latlng.lng) / NM_KM))); onDraft({ name: `AOI ${center.lat.toFixed(1)}, ${center.lng.toFixed(1)}`, lat: center.lat, lon: center.lng, radius_nm, user: true }); } }); return null;
}
function AoiCircle({ region, drawing, onZoom }: { region: Region; drawing: boolean; onZoom: (region: Region) => void }) {
  const radius = region.radius_nm * NM_KM * 1000;
  const zoom = () => { if (!drawing) onZoom(region); };
  return <><Circle center={[region.lat, region.lon]} radius={radius} renderer={renderer} bubblingMouseEvents
    pathOptions={{ color: aoiColor, weight: 1.5, dashArray: "6 4", fill: true, fillOpacity: .08 }} interactive={false} />
    <Marker position={[region.lat, region.lon]} icon={aoiCenterIcon} zIndexOffset={1000} eventHandlers={{ click: zoom }} /></>;
}
function ClusterLayer<T extends Point>({ values, zoom, color, pointColor, renderPoint, markerIcon, onSelect }: { values: T[]; zoom: number; color: string; pointColor?: (value: T) => string; renderPoint: (value: T) => MapDetail; markerIcon?: (value: T) => L.DivIcon | undefined; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  return <>{groups.map((group, index) => { const item = group.items[0]; const icon = group.items.length === 1 && zoom >= 6 ? markerIcon?.(item) : undefined; const dotColor = pointColor?.(item) ?? color; return icon ? <Marker key={`icon-${index}-${item.lat}-${item.lon}`} position={[group.lat, group.lon]} icon={icon} eventHandlers={{ click: () => onSelect(renderPoint(item)) }} /> : group.items.length === 1 ? <CircleMarker key={`point-${index}-${item.lat}-${item.lon}`} center={[group.lat, group.lon]} renderer={renderer} radius={4} pathOptions={{ color: dotColor, fillColor: dotColor, fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect(renderPoint(item)) }} /> : <Marker key={`cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, color)} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />; })}</>;
}

function TrackLayer({ values, zoom, onSelect }: { values: Track[]; zoom: number; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  const detail = (item: Track): MapDetail => ({ title: item.callsign || "Unidentified aircraft", lines: [`${item.ac_type || "Unknown type"} · ${aircraftKind(item.ac_type)} · ICAO ${item.hex || "?"}`, `${item.military ? "MILITARY" : "Civil"} · ${item.alt_ft ?? "ground"} ft · ${item.gs_kt ?? "?"} kt`, `Heading ${item.track_deg ?? "?"}° · ${item.registration || "no registration"}`], entityId: item.id, recordRef: { kind: "adsb", id: item.id }, point: [item.lat, item.lon], href: item.hex ? `https://adsb.lol/?icao=${encodeURIComponent(item.hex)}` : undefined, hrefLabel: "Open ADS-B source" });
  return <>{groups.map((group, index) => group.items.length === 1 ? <Marker key={`track-${group.items[0].id}`} position={[group.lat, group.lon]} icon={planeIcon(group.items[0])} eventHandlers={{ click: () => onSelect(detail(group.items[0])) }} /> : <Marker key={`track-cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, "#5cc7da")} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />)}</>;
}

function ThermalLayer({ values, zoom, onSelect }: { values: Firms[]; zoom: number; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  const detail = (item: Firms): MapDetail => { const novel = (item.novelty ?? 0) >= .9; return { title: `${novel ? "NEW " : "Routine "}thermal anomaly`, lines: [`${item.ts.slice(0, 16)}Z · ${item.frp ?? "?"} MW`, `${item.satellite ?? "?"} ${item.daynight === "N" ? "night" : "day"}`, `Coordinates ${item.lat.toFixed(4)}, ${item.lon.toFixed(4)}`, novel ? "Absent from the two-day baseline." : "Present in the two-day baseline." ], entityId: item.id, recordRef: { kind: "firms", id: item.id }, point: [item.lat, item.lon], href: `https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@${item.lon.toFixed(4)},${item.lat.toFixed(4)},10z`, hrefLabel: "Open NASA FIRMS" }; };
  return <>{groups.map((group, index) => { const item = group.items[0]; const novel = (item.novelty ?? 0) >= .9; return group.items.length === 1 && zoom >= 5 ? <Marker key={`thermal-${item.lat}-${item.lon}-${item.ts}`} position={[group.lat, group.lon]} icon={thermalIcon(novel)} eventHandlers={{ click: () => onSelect(detail(item)) }} /> : group.items.length === 1 ? <CircleMarker key={`thermal-point-${item.lat}-${item.lon}-${item.ts}`} center={[group.lat, group.lon]} renderer={renderer} radius={novel ? 4 : 3} pathOptions={{ color: novel ? "#f87171" : "#8d2b24", fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect(detail(item)) }} /> : <Marker key={`thermal-cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, "#f87171")} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />; })}</>;
}

export function OperationalMap({ events, tracks, alerts, firms, sar, sarCore, tails, regions, layers, viewport, assessments = [], incidents = [], selectedIncident = null, onIncidentSelect, filterAoi, drawing, focus, satelliteDay, replayBounds, compareSelection = [], compareVerdict, compareArticleMatch, onDraft, onSelect, onAoiZoom, onBackgroundClick, onViewport, onLayerToggle }: Props) {
  const [rfOpen, setRfOpen] = useState(false);
  useEffect(() => { if (!layers.rf) setRfOpen(false); }, [layers.rf]);
  const include = (lat: number, lon: number) => !filterAoi || regions.length === 0 || regions.some((region) => distanceKm(lat, lon, region.lat, region.lon) <= region.radius_nm * NM_KM);
  const imagery = satelliteDay ?? new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const trackById = useMemo(() => new Map(tracks.map((track) => [track.id, track])), [tracks]);
  const evidencePoints = useMemo(() => new Map<string, [number, number]>([
    ...events.map((item) => [item.id, [item.lat, item.lon] as [number, number]] as const),
    ...tracks.map((item) => [item.id, [item.lat, item.lon] as [number, number]] as const),
    ...firms.map((item) => [item.id, [item.lat, item.lon] as [number, number]] as const),
  ]), [events, tracks, firms]);
  const filteredEvents = useMemo(() => events.filter((item) => include(item.lat, item.lon)), [events, filterAoi, regions]);
  const filteredTracks = useMemo(() => tracks.filter((item) => include(item.lat, item.lon)), [tracks, filterAoi, regions]);
  return <div className="relative h-full w-full"><MapContainer center={[35, 10]} zoom={2} worldCopyJump className="h-full w-full"><FocusMap focus={focus} /><ViewportReporter onViewport={onViewport} /><AreaDrawer enabled={drawing} onDraft={onDraft} /><MapBackgroundClick onClick={onBackgroundClick} /><TileLayer className="dark-tiles" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap contributors" maxZoom={18} />
    {layers.imagery && <TileLayer url={`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/${imagery}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`} attribution={`NASA GIBS VIIRS ${imagery}`} maxNativeZoom={9} maxZoom={18} opacity={.85} />}
    {layers.rf && <Marker position={[26.55, 56.45]} icon={rfIcon} title="RF sample · Strait of Hormuz · Open spectrum" alt="Open simulated RF spectrum" zIndexOffset={1100} eventHandlers={{ click: () => { if (!drawing) setRfOpen(true); } }} />}
    {regions.map((region) => <AoiCircle key={region.id} region={region} drawing={drawing} onZoom={onAoiZoom} />)}
    {layers.firms && <ThermalLayer values={firms} zoom={viewport.zoom} onSelect={onSelect} />}
    {layers.sar && <ClusterLayer values={sarCore} zoom={viewport.zoom} color="#f2bb57" onSelect={onSelect} renderPoint={(item) => ({ title: "Radar ship · strait-core scene", lines: [`~${item.length_m ?? "?"} m`, `Sentinel-1 ${item.ts.slice(0, 16)}Z`] })} />}
    {layers.sar && <ClusterLayer values={sar} zoom={viewport.zoom} color="#e5e7df" onSelect={onSelect} renderPoint={(item) => ({ title: "Radar ship detection", lines: [`~${item.length_m ?? "?"} m · contrast ${item.contrast ?? "?"}`, `Sentinel-1 ${item.ts.slice(0, 16)}Z`] })} />}
    {layers.events && <ClusterLayer values={filteredEvents} zoom={viewport.zoom} color={eventColor} pointColor={(item) => isSocial(item) ? "#e879f9" : eventColor} markerIcon={(item) => isSocial(item) ? socialIcon(item) ?? osintIcon : osintIcon} onSelect={onSelect} renderPoint={(item) => ({ title: isSocial(item) ? socialTitle(item) : item.root_label, lines: [item.place, `Goldstein ${item.goldstein ?? "–"} · tone ${item.tone?.toFixed(1) ?? "–"}`, `Themes: ${item.themes?.slice(0, 8).join(", ") || "–"}`], entityId: item.id, recordRef: { kind: socialPlatform(item) ?? "gdelt", id: item.id }, point: [item.lat, item.lon], href: isSocial(item) || !isSocialLanding(item.url) ? item.url : undefined, hrefLabel: "Open source" })} />}
    {layers.tracks && <TrackLayer values={filteredTracks} zoom={viewport.zoom} onSelect={onSelect} />}
    {layers.tracks && tails.map((tail, index) => <Polyline key={`tail-${index}`} positions={tail.coords} renderer={renderer} pathOptions={{ color: tail.military ? "#1d4ed8" : "#5cc7da", weight: 2.5, opacity: .85 }} />)}
    {layers.links && alerts.filter((item) => include(item.lat, item.lon)).slice(0, 75).flatMap((alert) => { const track = trackById.get(alert.aircraft_id); return track ? [<Polyline key={`${alert.aircraft_id}-${alert.event_id}`} positions={[[alert.lat, alert.lon], [track.lat, track.lon]]} renderer={renderer} pathOptions={{ color: "#eab85a", weight: 1 + 2 * alert.score, opacity: .45, dashArray: "5 5" }} />] : []; })}
    {layers.links && assessments.slice(0, 100).flatMap((assessment) => { const left = evidencePoints.get(assessment.left_id); const right = evidencePoints.get(assessment.right_id); const color = assessment.verdict === "SUPPORTED" ? "#94c973" : assessment.verdict === "PLAUSIBLE" ? "#eab85a" : assessment.has_article_match ? "#a78bfa" : "#df5e55"; return left && right ? [<Polyline key={`assessment-${assessment.id}`} positions={[left, right]} renderer={renderer} pathOptions={{ color, weight: 2 + 3 * assessment.evidence_strength, opacity: .85, dashArray: assessment.verdict === "SUPPORTED" ? undefined : "8 4" }} />] : []; })}
    {compareSelection.length === 2 && compareSelection[0].point && compareSelection[1].point && <Polyline positions={[compareSelection[0].point, compareSelection[1].point]} renderer={renderer} pathOptions={{ color: compareVerdict === "SUPPORTED" ? "#94c973" : compareVerdict === "PLAUSIBLE" ? "#eab85a" : compareArticleMatch ? "#a78bfa" : compareVerdict ? "#df5e55" : "#a78bfa", weight: 4, opacity: .9, dashArray: compareVerdict === "SUPPORTED" ? undefined : "7 5" }} />}
    {replayBounds && <Rectangle bounds={[[replayBounds[0], replayBounds[1]], [replayBounds[2], replayBounds[3]]]} renderer={renderer} pathOptions={{ color: eventColor, weight: 1, fill: false, dashArray: "4 4" }} />}
    {layers.incidents && incidents.map((inc) => { const cell = inc.cells[0]; if (!cell) return null; const color = INCIDENT_COLOR[inc.state] ?? "#df5e55";
      const departed = Object.entries(inc.streams).filter(([, st]) => st.departed).map(([k, st]) => `${STREAM_SHORT[k] ?? k}${st.best?.z != null ? ` z${st.best.z}` : ""}`);
      const label = `${inc.id.replace("incident:", "#")} · ${inc.state.replace("_", " ").toUpperCase()} · ${departed.join(", ") || "no stream departed"}`;
      const icon = L.divIcon({ className: "", iconAnchor: [0, 0], html: `<div style="font:600 10px ui-monospace,monospace;letter-spacing:.04em;color:#101710;background:${color};padding:1px 5px;border:1px solid #101710;white-space:nowrap;max-width:320px;overflow:hidden;text-overflow:ellipsis;cursor:pointer">${label.replace(/</g, "&lt;")}</div>` });
      return <Marker key={`label:${inc.id}`} position={[cell[0] + 1, cell[1]]} icon={icon} zIndexOffset={1200} eventHandlers={{ click: () => { if (!drawing) onIncidentSelect?.(inc.id); } }} />;
    })}
    {layers.incidents && incidents.flatMap((inc) => inc.cells.map((cell) => {
      const color = INCIDENT_COLOR[inc.state] ?? "#df5e55"; const on = selectedIncident === inc.id;
      const departed = Object.entries(inc.streams).filter(([, st]) => st.departed).map(([k, st]) => `${STREAM_SHORT[k] ?? k}${st.best?.z != null ? ` z${st.best.z}` : ""}`);
      return <Rectangle key={`${inc.id}:${cell[0]}:${cell[1]}`} bounds={[[cell[0], cell[1]], [cell[0] + 1, cell[1] + 1]]} renderer={renderer}
        pathOptions={{ color, weight: on ? 3 : 1.5, fillColor: color, fillOpacity: on ? 0.28 : 0.14, dashArray: inc.state === "recovering" ? "4 4" : undefined }}
        eventHandlers={{ click: () => { if (!drawing) onIncidentSelect?.(inc.id); } }}>
        <Tooltip sticky direction="top" opacity={0.95}><span className="font-mono text-[10px]">{inc.id.replace("incident:", "#")} · {inc.state.replace("_", " ")} · {departed.join(", ") || "no stream departed"}<br />{inc.explanations[0]?.title ?? ""}</span></Tooltip>
      </Rectangle>;
    }))}
  </MapContainer><div className="absolute right-3 top-3 z-[1000] grid grid-cols-2 gap-1 border border-line bg-panel/95 p-1">{(Object.keys(layers) as Array<keyof LayerState>).map((layer) => <button key={layer} onClick={() => onLayerToggle(layer)} aria-pressed={layers[layer]} className={`border px-2 py-1 font-mono text-[9px] uppercase ${layers[layer] ? "border-command/50 text-command" : "border-line text-muted"}`}>{LAYER_LABELS[layer]}</button>)}</div>{layers.rf && rfOpen && <RfSpectrumDialog onClose={() => setRfOpen(false)} />}</div>;
}
