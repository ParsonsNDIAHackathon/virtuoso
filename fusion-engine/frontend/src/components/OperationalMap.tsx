import { useCallback, useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, Rectangle, TileLayer, useMap, useMapEvents } from "react-leaflet";
import type { Alert, Event, Firms, Region, Sar, Tail, Track, Viewport } from "../lib/types";
import { distanceKm } from "../lib/utils";

const NM_KM = 1.852;
const renderer = L.canvas({ padding: .5 });
const eventColor = "#eab85a";
const clusterIcon = (count: number, color: string) => L.divIcon({ className: "", iconSize: [34, 34], iconAnchor: [17, 17], html: `<span style="display:grid;place-items:center;width:34px;height:34px;border-radius:50%;border:2px solid ${color};background:#101710e8;color:${color};font:600 10px monospace">${count > 999 ? "999+" : count}</span>` });

export type MapDetail = { title: string; lines: string[]; href?: string; hrefLabel?: string };
export type LayerState = { events: boolean; tracks: boolean; firms: boolean; sar: boolean; links: boolean; imagery: boolean };
type Cluster<T> = { lat: number; lon: number; items: T[] };
type Point = { lat: number; lon: number };
type Props = {
  events: Event[]; tracks: Track[]; alerts: Alert[]; firms: Firms[]; sar: Sar[]; tails: Tail[]; regions: Region[]; layers: LayerState; viewport: Viewport;
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
function ClusterLayer<T extends Point>({ values, zoom, color, renderPoint, onSelect }: { values: T[]; zoom: number; color: string; renderPoint: (value: T) => MapDetail; onSelect: (detail: MapDetail) => void }) {
  const map = useMap(); const groups = useMemo(() => cluster(values, zoom), [values, zoom]);
  return <>{groups.map((group, index) => group.items.length === 1 ? <CircleMarker key={`point-${index}-${group.items[0].lat}-${group.items[0].lon}`} center={[group.lat, group.lon]} renderer={renderer} radius={4} pathOptions={{ color, fillColor: color, fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect(renderPoint(group.items[0])) }} /> : <Marker key={`cluster-${index}`} position={[group.lat, group.lon]} icon={clusterIcon(group.items.length, color)} eventHandlers={{ click: () => map.setView([group.lat, group.lon], Math.min(zoom + 2, 9)) }} />)}</>;
}

export function OperationalMap({ events, tracks, alerts, firms, sar, tails, regions, layers, viewport, filterAoi, drawing, focus, satelliteDay, replayBounds, onDraft, onSelect, onViewport, onLayerToggle }: Props) {
  const include = (lat: number, lon: number) => !filterAoi || regions.length === 0 || regions.some((region) => distanceKm(lat, lon, region.lat, region.lon) <= region.radius_nm * NM_KM);
  const imagery = satelliteDay ?? new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const trackById = useMemo(() => new Map(tracks.map((track) => [track.id, track])), [tracks]);
  const filteredEvents = useMemo(() => events.filter((item) => include(item.lat, item.lon)), [events, filterAoi, regions]);
  const filteredTracks = useMemo(() => tracks.filter((item) => include(item.lat, item.lon)), [tracks, filterAoi, regions]);
  return <div className="relative h-full w-full"><MapContainer center={[35, 10]} zoom={2} worldCopyJump className="h-full w-full"><FocusMap focus={focus} /><ViewportReporter onViewport={onViewport} /><AreaDrawer enabled={drawing} onDraft={onDraft} /><TileLayer className="dark-tiles" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap contributors" maxZoom={18} />
    {layers.imagery && <TileLayer url={`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/${imagery}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`} attribution={`NASA GIBS VIIRS ${imagery}`} maxNativeZoom={9} maxZoom={18} opacity={.85} />}
    {regions.map((region) => <Circle key={region.id} center={[region.lat, region.lon]} radius={region.radius_nm * NM_KM * 1000} renderer={renderer} pathOptions={{ color: region.user ? "#94c973" : eventColor, weight: 1.2, dashArray: "6 4", fillOpacity: .04 }} />)}
    {layers.firms && <ClusterLayer values={firms} zoom={viewport.zoom} color="#df5e55" onSelect={onSelect} renderPoint={(item) => ({ title: `${(item.novelty ?? 0) >= .9 ? "NEW " : ""}Thermal anomaly`, lines: [`${item.ts.slice(0, 16)}Z · ${item.frp ?? "?"} MW`, `${item.satellite ?? "?"} ${item.daynight === "N" ? "night" : "day"}`] })} />}
    {layers.sar && <ClusterLayer values={sar} zoom={viewport.zoom} color="#e5e7df" onSelect={onSelect} renderPoint={(item) => ({ title: "Radar ship detection", lines: [`~${item.length_m ?? "?"} m · contrast ${item.contrast ?? "?"}`, `Sentinel-1 ${item.ts.slice(0, 16)}Z`] })} />}
    {layers.events && <ClusterLayer values={filteredEvents} zoom={viewport.zoom} color={eventColor} onSelect={onSelect} renderPoint={(item) => ({ title: item.source_domain?.startsWith("t.me/") ? `Telegram · ${item.source_domain.slice(5)}` : item.root_label, lines: [item.place, `Goldstein ${item.goldstein ?? "–"} · tone ${item.tone?.toFixed(1) ?? "–"}`, `Themes: ${item.themes?.slice(0, 8).join(", ") || "–"}`], href: item.url, hrefLabel: "Open source" })} />}
    {layers.tracks && <ClusterLayer values={filteredTracks} zoom={viewport.zoom} color="#5cc7da" onSelect={onSelect} renderPoint={(item) => ({ title: item.callsign || "Unidentified aircraft", lines: [`${item.registration || ""} · ${item.ac_type || "?"} · ICAO ${item.hex || "?"}`, `${item.military ? "MILITARY" : "Civil"} · ${item.alt_ft ?? "ground"} ft · ${item.gs_kt ?? "?"} kt`, `source ${item.source ?? "?"} · ${item.ts ?? ""}`] })} />}
    {layers.tracks && tails.map((tail, index) => <Polyline key={`tail-${index}`} positions={tail.coords} renderer={renderer} pathOptions={{ color: tail.military ? "#df5e55" : "#5cc7da", weight: 1, opacity: .45 }} />)}
    {layers.links && alerts.filter((item) => include(item.lat, item.lon)).slice(0, 75).flatMap((alert) => { const track = trackById.get(alert.aircraft_id); return track ? [<Polyline key={`${alert.aircraft_id}-${alert.event_id}`} positions={[[alert.lat, alert.lon], [track.lat, track.lon]]} renderer={renderer} pathOptions={{ color: alert.score > .5 ? "#df5e55" : eventColor, weight: 1 + 3 * alert.score, opacity: .7 }} />] : []; })}
    {replayBounds && <Rectangle bounds={[[replayBounds[0], replayBounds[1]], [replayBounds[2], replayBounds[3]]]} renderer={renderer} pathOptions={{ color: eventColor, weight: 1, fill: false, dashArray: "4 4" }} />}
  </MapContainer><div className="absolute right-3 top-3 z-[1000] grid grid-cols-2 gap-1 border border-line bg-panel/95 p-1">{(Object.keys(layers) as Array<keyof LayerState>).map((layer) => <button key={layer} onClick={() => onLayerToggle(layer)} className={`border px-2 py-1 font-mono text-[9px] uppercase ${layers[layer] ? "border-command/50 text-command" : "border-line text-muted"}`}>{layer}</button>)}</div></div>;
}
