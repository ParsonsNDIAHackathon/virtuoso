import { useCallback, useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, Rectangle, TileLayer, useMap, useMapEvents } from "react-leaflet";
import type { Alert, Event, Firms, Region, Sar, Tail, Track, Viewport } from "../lib/types";
import { distanceKm } from "../lib/utils";

const NM_KM = 1.852;
const renderer = L.canvas({ padding: .5 });
const eventColor = "#eab85a";
const LAYER_LABELS: Record<keyof LayerState, string> = { events: "OSINT", tracks: "ADS-B", firms: "Thermal", sar: "SAR", links: "Links", imagery: "Imagery" };
const clusterIcon = (count: number, color: string) => L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17], html: `<span style="display:grid;place-items:center;width:34px;height:34px;border-radius:50%;border:2px solid ${color};background:#101710e8;color:${color};font:600 10px monospace">${count > 999 ? "999+" : count}</span>` });
const planeIcons = new Map<string, L.DivIcon>();
const thermalIcons = new Map<string, L.DivIcon>();
const telegramIcon = L.divIcon({
  className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: '<svg viewBox="0 0 32 32" width="22" height="22" style="display:block;filter:drop-shadow(0 0 2px #101710)" aria-label="Telegram post"><circle cx="16" cy="16" r="14" fill="#2387c6" stroke="#101710" stroke-width="1.5"/><path d="m7 15.1 17-6.7c.8-.3 1.5.2 1.2 1.2l-3.1 14.1c-.2 1-1 1.2-1.8.7l-4.6-3.4-2.2 2.1c-.2.2-.4.4-.9.4l.3-4.8 8.8-8c.4-.4-.1-.6-.6-.3L10.2 17l-4.7-1.5c-1-.3-1-1 .2-1.4z" fill="#effaff"/></svg>',
});

function isTelegram(event: Event) { return event.source_domain?.startsWith("t.me/") || event.url?.includes("t.me/"); }

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
  const kind = aircraftKind(track.ac_type); const heading = Math.round((track.track_deg ?? 0) / 10) * 10;
  const color = track.military ? "#df5e55" : "#5cc7da"; const key = `${kind}:${heading}:${color}`;
  const cached = planeIcons.get(key); if (cached) return cached;
  const shapes: Record<string, string> = {
    fixed: '<path d="M16 2 20 12 29 16 20 19 18 30h-4l-2-11L3 16l9-4L16 2z"/>',
    airliner: '<path d="M16 1 21 12 31 16 21 19 19 31h-6l-2-12L1 16l10-4L16 1z"/>',
    heavy: '<path d="M16 2 21 12 31 15v3l-10 2-2 10h-6l-2-10-10-2v-3l10-3L16 2z"/><path d="M11 21h10" stroke="currentColor" stroke-width="2"/>',
    fighter: '<path d="M16 2 28 29 16 23 4 29 16 2z"/><path d="M16 8v15" stroke="#101710" stroke-width="1.5"/>',
    helicopter: '<path d="M10 17h12l4 4H7l3-4z"/><circle cx="16" cy="16" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 12V4M5 5h22M16 4l-4 3m4-3 4 3M8 21l-3 6m15-6 3 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  };
  const icon = L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: `<svg viewBox="0 0 32 32" width="22" height="22" fill="currentColor" style="display:block;color:${color};filter:drop-shadow(0 0 2px #101710);transform:rotate(${heading}deg);transform-origin:center" aria-label="${kind} aircraft">${shapes[kind]}</svg>` });
  planeIcons.set(key, icon); return icon;
}

export type MapDetail = { title: string; lines: string[]; href?: string; hrefLabel?: string };
export type LayerState = { events: boolean; tracks: boolean; firms: boolean; sar: boolean; links: boolean; imagery: boolean };
type Cluster<T> = { lat: number; lon: number; items: T[] };
type Point = { lat: number; lon: number };
type Props = {
  events: Event[]; tracks: Track[]; alerts: Alert[]; firms: Firms[]; sar: Sar[]; sarCore: Sar[]; tails: Tail[]; regions: Region[]; layers: LayerState; viewport: Viewport;
  filterAoi: boolean; drawing: boolean; focus?: [number, number, number]; satelliteDay?: string; replayBounds?: [number, number, number, number];
  onDraft: (draft: Omit<Region, "id">) => void; onSelect: (detail: MapDetail) => void; onViewport: (viewport: Viewport) => void; onLayerToggle: (layer: keyof LayerState) => void;
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
function ClusterLayer<T extends Point>({ values, zoom, color, renderPoint, markerIcon, onSelect }: { values: T[]; zoom: number; color: string; renderPoint: (value: T) => MapDetail; markerIcon?: (value: T) => L.DivIcon | undefined; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  return <>{groups.map((group, index) => { const item = group.items[0]; const icon = group.items.length === 1 && zoom >= 6 ? markerIcon?.(item) : undefined; return icon ? <Marker key={`icon-${index}-${item.lat}-${item.lon}`} position={[group.lat, group.lon]} icon={icon} eventHandlers={{ click: () => onSelect(renderPoint(item)) }} /> : group.items.length === 1 ? <CircleMarker key={`point-${index}-${item.lat}-${item.lon}`} center={[group.lat, group.lon]} renderer={renderer} radius={4} pathOptions={{ color, fillColor: color, fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect(renderPoint(item)) }} /> : <Marker key={`cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, color)} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />; })}</>;
}

function TrackLayer({ values, zoom, onSelect }: { values: Track[]; zoom: number; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  const detail = (item: Track): MapDetail => ({ title: item.callsign || "Unidentified aircraft", lines: [`${item.ac_type || "Unknown type"} · ${aircraftKind(item.ac_type)} · ICAO ${item.hex || "?"}`, `${item.military ? "MILITARY" : "Civil"} · ${item.alt_ft ?? "ground"} ft · ${item.gs_kt ?? "?"} kt`, `Heading ${item.track_deg ?? "?"}° · ${item.registration || "no registration"}`] });
  return <>{groups.map((group, index) => group.items.length === 1 && zoom >= 6 ? <Marker key={`track-${group.items[0].id}`} position={[group.lat, group.lon]} icon={planeIcon(group.items[0])} eventHandlers={{ click: () => onSelect(detail(group.items[0])) }} /> : group.items.length === 1 ? <CircleMarker key={`track-point-${group.items[0].id}`} center={[group.lat, group.lon]} renderer={renderer} radius={4} pathOptions={{ color: group.items[0].military ? "#df5e55" : "#5cc7da", fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect(detail(group.items[0])) }} /> : <Marker key={`track-cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, "#5cc7da")} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />)}</>;
}

function ThermalLayer({ values, zoom, onSelect }: { values: Firms[]; zoom: number; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  const detail = (item: Firms): MapDetail => { const novel = (item.novelty ?? 0) >= .9; return { title: `${novel ? "NEW " : "Routine "}thermal anomaly`, lines: [`${item.ts.slice(0, 16)}Z · ${item.frp ?? "?"} MW`, `${item.satellite ?? "?"} ${item.daynight === "N" ? "night" : "day"}`, novel ? "Absent from the two-day baseline." : "Present in the two-day baseline." ], href: `https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@${item.lon.toFixed(4)},${item.lat.toFixed(4)},10z`, hrefLabel: "Open NASA FIRMS" }; };
  return <>{groups.map((group, index) => { const item = group.items[0]; const novel = (item.novelty ?? 0) >= .9; return group.items.length === 1 && zoom >= 5 ? <Marker key={`thermal-${item.lat}-${item.lon}-${item.ts}`} position={[group.lat, group.lon]} icon={thermalIcon(novel)} eventHandlers={{ click: () => onSelect(detail(item)) }} /> : group.items.length === 1 ? <CircleMarker key={`thermal-point-${item.lat}-${item.lon}-${item.ts}`} center={[group.lat, group.lon]} renderer={renderer} radius={novel ? 4 : 3} pathOptions={{ color: novel ? "#f87171" : "#8d2b24", fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect(detail(item)) }} /> : <Marker key={`thermal-cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, "#f87171")} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />; })}</>;
}

export function OperationalMap({ events, tracks, alerts, firms, sar, sarCore, tails, regions, layers, viewport, filterAoi, drawing, focus, satelliteDay, replayBounds, onDraft, onSelect, onViewport, onLayerToggle }: Props) {
  const include = (lat: number, lon: number) => !filterAoi || regions.length === 0 || regions.some((region) => distanceKm(lat, lon, region.lat, region.lon) <= region.radius_nm * NM_KM);
  const imagery = satelliteDay ?? new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const trackById = useMemo(() => new Map(tracks.map((track) => [track.id, track])), [tracks]);
  const filteredEvents = useMemo(() => events.filter((item) => include(item.lat, item.lon)), [events, filterAoi, regions]);
  const filteredTracks = useMemo(() => tracks.filter((item) => include(item.lat, item.lon)), [tracks, filterAoi, regions]);
  return <div className="relative h-full w-full"><MapContainer center={[35, 10]} zoom={2} worldCopyJump className="h-full w-full"><FocusMap focus={focus} /><ViewportReporter onViewport={onViewport} /><AreaDrawer enabled={drawing} onDraft={onDraft} /><TileLayer className="dark-tiles" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap contributors" maxZoom={18} />
    {layers.imagery && <TileLayer url={`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/${imagery}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`} attribution={`NASA GIBS VIIRS ${imagery}`} maxNativeZoom={9} maxZoom={18} opacity={.85} />}
    {regions.map((region) => <Circle key={region.id} center={[region.lat, region.lon]} radius={region.radius_nm * NM_KM * 1000} renderer={renderer} pathOptions={{ color: region.user ? "#94c973" : eventColor, weight: 1.2, dashArray: "6 4", fillOpacity: .04 }} />)}
    {layers.firms && <ThermalLayer values={firms} zoom={viewport.zoom} onSelect={onSelect} />}
    {layers.sar && <ClusterLayer values={sarCore} zoom={viewport.zoom} color="#f2bb57" onSelect={onSelect} renderPoint={(item) => ({ title: "Radar ship · strait-core scene", lines: [`~${item.length_m ?? "?"} m`, `Sentinel-1 ${item.ts.slice(0, 16)}Z`] })} />}
    {layers.sar && <ClusterLayer values={sar} zoom={viewport.zoom} color="#e5e7df" onSelect={onSelect} renderPoint={(item) => ({ title: "Radar ship detection", lines: [`~${item.length_m ?? "?"} m · contrast ${item.contrast ?? "?"}`, `Sentinel-1 ${item.ts.slice(0, 16)}Z`] })} />}
    {layers.events && <ClusterLayer values={filteredEvents} zoom={viewport.zoom} color={eventColor} markerIcon={(item) => isTelegram(item) ? telegramIcon : undefined} onSelect={onSelect} renderPoint={(item) => ({ title: isTelegram(item) ? `Telegram · ${item.source_domain?.replace(/^t\.me\//, "") || "post"}` : item.root_label, lines: [item.place, `Goldstein ${item.goldstein ?? "–"} · tone ${item.tone?.toFixed(1) ?? "–"}`, `Themes: ${item.themes?.slice(0, 8).join(", ") || "–"}`], href: item.url, hrefLabel: "Open source" })} />}
    {layers.tracks && <TrackLayer values={filteredTracks} zoom={viewport.zoom} onSelect={onSelect} />}
    {layers.tracks && tails.map((tail, index) => <Polyline key={`tail-${index}`} positions={tail.coords} renderer={renderer} pathOptions={{ color: tail.military ? "#df5e55" : "#5cc7da", weight: 1, opacity: .45 }} />)}
    {layers.links && alerts.filter((item) => include(item.lat, item.lon)).slice(0, 75).flatMap((alert) => { const track = trackById.get(alert.aircraft_id); return track ? [<Polyline key={`${alert.aircraft_id}-${alert.event_id}`} positions={[[alert.lat, alert.lon], [track.lat, track.lon]]} renderer={renderer} pathOptions={{ color: alert.score > .5 ? "#df5e55" : eventColor, weight: 1 + 3 * alert.score, opacity: .7 }} />] : []; })}
    {replayBounds && <Rectangle bounds={[[replayBounds[0], replayBounds[1]], [replayBounds[2], replayBounds[3]]]} renderer={renderer} pathOptions={{ color: eventColor, weight: 1, fill: false, dashArray: "4 4" }} />}
  </MapContainer><div className="absolute right-3 top-3 z-[1000] grid grid-cols-2 gap-1 border border-line bg-panel/95 p-1">{(Object.keys(layers) as Array<keyof LayerState>).map((layer) => <button key={layer} onClick={() => onLayerToggle(layer)} className={`border px-2 py-1 font-mono text-[9px] uppercase ${layers[layer] ? "border-command/50 text-command" : "border-line text-muted"}`}>{LAYER_LABELS[layer]}</button>)}</div></div>;
}
