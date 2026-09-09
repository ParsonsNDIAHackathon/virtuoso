import { useEffect, useMemo, useRef, useState } from "react";
import type { Event, Firms, Track } from "../lib/types";
import type { MapDetail } from "./OperationalMap";
import { Panel } from "./ui/panel";

export type SourceKey = "gdelt" | "social" | "adsb" | "firms" | "candidates" | "openai";
const TITLE: Record<SourceKey, string> = { gdelt: "GDELT · OSINT records", social: "Social · OSINT records", adsb: "ADS-B · aircraft records", firms: "FIRMS · thermal records", candidates: "Fusion candidates", openai: "AI assessments" };

function platformOf(id: string) {
  if (id.startsWith("tg:")) return "telegram";
  if (id.startsWith("reddit:")) return "reddit";
  if (id.startsWith("bsky:")) return "bluesky";
  if (id.startsWith("mastodon:") || id.startsWith("md:")) return "mastodon";
  return null;
}
const isSocial = (e: Event) => platformOf(e.id) !== null;
const when = (ts?: string) => (ts ? ts.slice(5, 16).replace("T", " ") + "Z" : "");

type Row = { id: string; tag: string; title: string; sub: string; ts: string; detail: MapDetail };

function eventRow(e: Event): Row {
  const p = platformOf(e.id);
  const title = p ? `${p} · ${(e.source_domain ?? "").replace(/^t\.me\//, "") || "post"}` : e.root_label;
  return { id: e.id, tag: p ?? (e.is_conflict ? "conflict" : "news"), title, sub: [e.place, p ? e.root_label : e.source_domain].filter(Boolean).join(" · "), ts: e.ts ?? "",
    detail: { title, lines: [e.place, `Goldstein ${e.goldstein ?? "–"} · tone ${e.tone?.toFixed(1) ?? "–"}`, `Themes: ${e.themes?.slice(0, 8).join(", ") || "–"}`], entityId: e.id, recordRef: { kind: p ?? "gdelt", id: e.id }, point: [e.lat, e.lon], href: e.url || undefined, hrefLabel: "Open source" } };
}
function trackRow(t: Track): Row {
  const title = t.callsign || t.registration || t.hex || "Unidentified aircraft";
  return { id: t.id, tag: t.military ? "military" : "civil", title, sub: [t.ac_type, t.alt_ft != null ? `${t.alt_ft} ft` : null, t.gs_kt != null ? `${t.gs_kt} kt` : null].filter(Boolean).join(" · "), ts: t.ts ?? "",
    detail: { title, lines: [`${t.ac_type || "Unknown type"} · ICAO ${t.hex || "?"}`, `${t.military ? "MILITARY" : "Civil"} · ${t.alt_ft ?? "ground"} ft · ${t.gs_kt ?? "?"} kt`, `Heading ${t.track_deg ?? "?"}° · ${t.registration || "no registration"}`], entityId: t.id, recordRef: { kind: "adsb", id: t.id }, point: [t.lat, t.lon] } };
}
function firmsRow(f: Firms): Row {
  const novel = (f.novelty ?? 0) >= 0.9;
  const title = `${novel ? "NEW" : "Routine"} thermal anomaly`;
  return { id: f.id, tag: novel ? "new" : "routine", title, sub: `${f.frp ?? "?"} MW · ${f.satellite ?? "?"} ${f.daynight === "N" ? "night" : "day"} · ${f.lat.toFixed(2)}, ${f.lon.toFixed(2)}`, ts: f.ts,
    detail: { title, lines: [`${f.ts.slice(0, 16)}Z · ${f.frp ?? "?"} MW`, `${f.satellite ?? "?"} ${f.daynight === "N" ? "night" : "day"}`, `Coordinates ${f.lat.toFixed(4)}, ${f.lon.toFixed(4)}`], recordRef: { kind: "firms", id: f.id }, point: [f.lat, f.lon] } };
}

/** The records behind a source chip in the strip: what the console has loaded for that source, newest first,
 *  each one opening in the Inspector and centring the map. Engine totals can exceed this list (the console
 *  loads a capped, recent window). */
export function SourceRecords({ source, engineCount, events, tracks, firms, onOpen, onClose, onResetMap, resetKey = 0 }: { source: SourceKey; engineCount?: number; events: Event[]; tracks: Track[]; firms: Firms[]; onOpen: (d: MapDetail) => void; onClose: () => void; onResetMap?: () => void; resetKey?: number }) {
  const [q, setQ] = useState("");
  const listRef = useRef<HTMLUListElement>(null);
  // Every chip click (even the same chip) shows that source fresh: filter cleared, list and column at the top.
  useEffect(() => { setQ(""); const body = listRef.current?.closest(".panel-body"); if (body) body.scrollTop = 0; const aside = listRef.current?.closest(".sidebar-resizable"); if (aside) aside.scrollTop = 0; }, [source, resetKey]);
  useEffect(() => { const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const rows = useMemo<Row[]>(() => {
    const list = source === "gdelt" ? events.filter((e) => !isSocial(e)).map(eventRow)
      : source === "social" ? events.filter(isSocial).map(eventRow)
      : source === "adsb" ? tracks.map(trackRow)
      : source === "firms" ? firms.map(firmsRow) : [];
    return list.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
  }, [source, events, tracks, firms]);
  const needle = q.trim().toLowerCase();
  const shown = needle ? rows.filter((r) => `${r.tag} ${r.title} ${r.sub}`.toLowerCase().includes(needle)) : rows;
  const byTag = useMemo(() => { const m: Record<string, number> = {}; for (const r of rows) m[r.tag] = (m[r.tag] ?? 0) + 1; return m; }, [rows]);
  const other = source === "candidates" || source === "openai";
  return <Panel panelId={`source-${source}`} className="max-h-[50vh] overflow-auto p-3 scrollbar">
    <div className="flex items-center gap-2">
      <h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">{TITLE[source]}</h2>
      <div className="ml-auto flex items-center gap-1">
        {onResetMap && <button onClick={onResetMap} className="h-7 border border-line px-2 font-mono text-[10px] uppercase tracking-wider text-muted hover:border-command/60 hover:text-ink" title="Return the map to the world view">Reset map</button>}
        <button onClick={onClose} className="h-7 border border-command/60 px-2 font-mono text-[10px] uppercase tracking-wider text-command hover:bg-command/10" aria-label="Close records" title="Close this list (Esc, or click the source chip again)">✕ Close</button>
      </div>
    </div>
    {other ? <p className="mt-1 text-[10px] text-muted">{source === "candidates" ? "Candidate pairs are listed in the Fusion candidates panel below." : "Assessed pairs are listed in the AI assessments panel below; toggle Show rejected to see every verdict."}</p> : <>
      <p className="mt-0.5 font-mono text-[10px] text-muted">{rows.length.toLocaleString()} loaded in the console{engineCount != null && engineCount !== rows.length ? ` · engine holds ${engineCount.toLocaleString()}` : ""}
        {Object.keys(byTag).length > 1 && <span> · {Object.entries(byTag).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>}</p>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter by place, outlet, platform, callsign…" className="mt-1 w-full border border-line bg-canvas px-2 py-1 font-mono text-[10px] text-ink placeholder:text-muted" />
      <ul ref={listRef} className="mt-1 divide-y divide-line/60">{shown.slice(0, 400).map((r) => <li key={r.id}>
        <button onClick={() => onOpen(r.detail)} className="flex w-full items-baseline gap-2 py-1 text-left hover:bg-white/[.04]">
          <span className="w-14 shrink-0 font-mono text-[9px] uppercase tracking-wider text-muted">{r.tag}</span>
          <span className="min-w-0 flex-1"><span className="block truncate text-[11px] text-ink">{r.title}</span><span className="block truncate text-[9px] text-muted">{r.sub}</span></span>
          <span className="shrink-0 font-mono text-[9px] text-muted">{when(r.ts)}</span>
        </button></li>)}
        {shown.length === 0 && <li className="py-2 text-[10px] text-muted">{rows.length === 0 ? "Nothing loaded for this source at the moment." : "No record matches the filter."}</li>}
        {shown.length > 400 && <li className="py-1 font-mono text-[9px] text-muted">first 400 of {shown.length} · narrow with the filter</li>}
      </ul></>}
  </Panel>;
}
