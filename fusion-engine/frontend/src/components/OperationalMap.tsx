import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { Circle, CircleMarker, LayersControl, MapContainer, Marker, Polyline, Rectangle, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { Alert, Event, Firms, Region, Sar, Tail, Track } from "../lib/types";
import { distanceKm } from "../lib/utils";

const NM_KM = 1.852;
const eventIcon = (social: boolean, conflict: boolean) => L.divIcon({ className: "", iconSize: [10, 10], iconAnchor: [5, 5], html: social ? `<span style="display:block;width:10px;height:10px;transform:rotate(45deg);background:${conflict ? "#d99add" : "#6f255e"};border:1px solid #101710"></span>` : "" });
const fireIcon = (novel: boolean) => L.divIcon({ className: "", iconSize: [14, 14], iconAnchor: [7, 9], html: `<span style="display:block;width:0;height:0;border-left:${novel ? 7 : 4}px solid transparent;border-right:${novel ? 7 : 4}px solid transparent;border-bottom:${novel ? 13 : 8}px solid ${novel ? "#ef4444" : "#8d2b24"};${novel ? "filter:drop-shadow(0 0 4px #ef4444);" : ""}"></span>` });
const shipIcon = (large: boolean) => L.divIcon({ className: "", iconSize: [12, 12], iconAnchor: [6, 6], html: `<span style="display:block;width:${large ? 12 : 8}px;height:${large ? 12 : 8}px;border:2px solid #e5e7df;background:${large ? "#e5e7dfaa" : "transparent"}"></span>` });

export type MapDetail = { title: string; lines: string[]; href?: string; hrefLabel?: string };
type Props = {
  events: Event[]; tracks: Track[]; alerts: Alert[]; firms: Firms[]; sar: Sar[]; tails: Tail[]; regions: Region[];
  filterAoi: boolean; drawing: boolean; focus?: [number, number, number]; satelliteDay?: string; replayBounds?: [number, number, number, number];
  onDraft: (draft: Omit<Region, "id">) => void; onSelect: (detail: MapDetail) => void;
};

function FocusMap({ focus }: { focus?: [number, number, number] }) {
  const map = useMap();
  useEffect(() => { if (focus) map.setView([focus[0], focus[1]], focus[2]); }, [focus, map]);
  return null;
}

function AreaDrawer({ enabled, onDraft }: { enabled: boolean; onDraft: Props["onDraft"] }) {
  const map = useMap();
  const origin = useRef<L.LatLng | null>(null);
  useEffect(() => { map.getContainer().style.cursor = enabled ? "crosshair" : ""; if (!enabled) map.dragging.enable(); }, [enabled, map]);
  useMapEvents({
    mousedown(event) { if (!enabled) return; origin.current = event.latlng; map.dragging.disable(); },
    mouseup(event) {
      if (!enabled || !origin.current) return;
      const center = origin.current; origin.current = null; map.dragging.enable();
      const radius_nm = Math.min(250, Math.max(5, Math.round(distanceKm(center.lat, center.lng, event.latlng.lat, event.latlng.lng) / NM_KM)));
      onDraft({ name: `AOI ${center.lat.toFixed(1)}, ${center.lng.toFixed(1)}`, lat: center.lat, lon: center.lng, radius_nm, user: true });
    },
  });
  return null;
}

export function OperationalMap({ events, tracks, alerts, firms, sar, tails, regions, filterAoi, drawing, focus, satelliteDay, replayBounds, onDraft, onSelect }: Props) {
  const include = (lat: number, lon: number) => !filterAoi || regions.length === 0 || regions.some((region) => distanceKm(lat, lon, region.lat, region.lon) <= region.radius_nm * NM_KM);
  const trackById = useMemo(() => new Map(tracks.map((track) => [track.id, track])), [tracks]);
  const imagery = satelliteDay ?? new Date(Date.now() - 86400000).toISOString().slice(0, 10);

  return <MapContainer center={[35, 10]} zoom={2} worldCopyJump className="h-full w-full">
    <FocusMap focus={focus} />
    <AreaDrawer enabled={drawing} onDraft={onDraft} />
    <TileLayer className="dark-tiles" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="© OpenStreetMap contributors" maxZoom={18} />
    <LayersControl position="topright">
      <LayersControl.Overlay name={`Satellite imagery (VIIRS ${imagery})`}>
        <TileLayer url={`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/${imagery}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`} attribution={`NASA GIBS VIIRS ${imagery}`} maxNativeZoom={9} maxZoom={18} opacity={0.85} />
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Areas of interest">
        <>{regions.map((region) => <Circle key={region.id} center={[region.lat, region.lon]} radius={region.radius_nm * NM_KM * 1000} pathOptions={{ color: region.user ? "#94c973" : "#eab85a", weight: 1.2, dashArray: "6 4", fillOpacity: 0.04 }}><Tooltip>{region.name} · {region.radius_nm} nm</Tooltip></Circle>)}</>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Thermal anomalies (FIRMS)">
        <>{firms.map((hotspot, index) => { const novel = (hotspot.novelty ?? 0) >= .9; return <Marker key={`${hotspot.lat}-${hotspot.lon}-${index}`} position={[hotspot.lat, hotspot.lon]} icon={fireIcon(novel)}><Tooltip>{novel ? "NEW " : ""}thermal anomaly · {hotspot.ts.slice(0, 16)}Z</Tooltip></Marker>; })}</>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Radar ship detections">
        <>{sar.map((ship, index) => <Marker key={`${ship.lat}-${ship.lon}-${index}`} position={[ship.lat, ship.lon]} icon={shipIcon((ship.length_m ?? 0) >= 150)}><Tooltip>Radar ship · ~{ship.length_m ?? "?"} m · {ship.ts.slice(0, 16)}Z</Tooltip></Marker>)}</>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="OSINT events">
        <>{events.filter((event) => include(event.lat, event.lon)).map((event) => {
          const social = event.source_domain?.startsWith("t.me/") ?? false;
          const tooltip = social ? `Telegram ${event.source_domain?.slice(5) ?? ""}: ${event.place}` : `${event.root_label}: ${event.place}`;
          const detail: MapDetail = social ? { title: `Telegram · ${event.source_domain?.slice(5) ?? ""}`, lines: [event.place, event.themes?.join(", ") || "–", `~${event.num_mentions ?? "?"}k views`], href: event.url, hrefLabel: "Open post" } : { title: event.root_label, lines: [`${event.place}${event.country ? ` (${event.country})` : ""}`, [event.actor1, event.actor2].filter(Boolean).join(" → ") || "–", `Goldstein ${event.goldstein ?? "–"} · tone ${event.tone?.toFixed(1) ?? "–"} · mentions ${event.num_mentions ?? "–"}`, `Themes: ${event.themes?.slice(0, 8).join(", ") || "–"}`], href: event.url, hrefLabel: "Open article" };
          return social ? <Marker key={event.id} position={[event.lat, event.lon]} icon={eventIcon(true, Boolean(event.is_conflict))} eventHandlers={{ click: () => onSelect(detail) }}><Tooltip>{tooltip}</Tooltip></Marker> : <CircleMarker key={event.id} center={[event.lat, event.lon]} radius={event.is_conflict ? 5 : 3} pathOptions={{ color: event.is_conflict ? "#eab85a" : "#9a622b", fillOpacity: event.is_conflict ? .8 : .5, weight: 1 }} eventHandlers={{ click: () => onSelect(detail) }}><Tooltip>{tooltip}</Tooltip></CircleMarker>;
        })}</>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Aircraft">
        <>{tails.map((tail, index) => <Polyline key={`tail-${index}`} positions={tail.coords} pathOptions={{ color: tail.military ? "#df5e55" : "#5cc7da", weight: 1, opacity: .45 }}><Tooltip>{tail.callsign || tail.r || tail.hex || "Aircraft"} · last 30 min</Tooltip></Polyline>)}{tracks.filter((track) => include(track.lat, track.lon)).map((track) => <CircleMarker key={track.id} center={[track.lat, track.lon]} radius={track.military ? 4 : 2.5} pathOptions={{ color: track.military ? "#df5e55" : "#5cc7da", fillOpacity: .8, weight: 1 }} eventHandlers={{ click: () => onSelect({ title: track.callsign || "Unidentified aircraft", lines: [`${track.registration || ""} · ${track.ac_type || "?"} · ICAO ${track.hex || "?"}`, `${track.military ? "MILITARY" : "Civil"} · ${track.alt_ft ?? "ground"} ft · ${track.gs_kt ?? "?"} kt · trk ${track.track_deg ?? "?"}°`, `source ${track.source ?? "?"} · ${track.ts ?? ""}`] }) }}><Tooltip>{track.callsign || track.registration || track.hex || "Aircraft"} · {track.alt_ft ?? "ground"} ft</Tooltip></CircleMarker>)}</>
      </LayersControl.Overlay>
      <LayersControl.Overlay checked name="Correlation links">
        <>{alerts.filter((alert) => include(alert.lat, alert.lon)).slice(0, 150).flatMap((alert) => { const track = trackById.get(alert.aircraft_id); return track ? [<Polyline key={`${alert.aircraft_id}-${alert.event_id}`} positions={[[alert.lat, alert.lon], [track.lat, track.lon]]} pathOptions={{ color: alert.score > .5 ? "#df5e55" : "#eab85a", weight: 1 + 3 * alert.score, opacity: .7 }}><Tooltip>{alert.score.toFixed(2)} · {alert.aircraft_label} ↔ {alert.event_label}</Tooltip></Polyline>] : []; })}</>
      </LayersControl.Overlay>
    </LayersControl>
    {replayBounds && <Rectangle bounds={[[replayBounds[0], replayBounds[1]], [replayBounds[2], replayBounds[3]]]} pathOptions={{ color: "#eab85a", weight: 1, fill: false, dashArray: "4 4" }} />}
  </MapContainer>;
}
