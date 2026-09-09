import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { CuratedClaim, CuratedSource } from "../lib/types";
import { Panel } from "./ui/panel";
import { Badge } from "./ui/badge";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDay = (d?: string | null) => { if (!d) return null; const [y, m, dd] = d.split("-"); return `${Number(dd)} ${MONTHS[Number(m) - 1]} ${y}`; };

// Timing labels follow the record's own precision; nothing is sharpened or shifted.
function claimTiming(c: CuratedClaim) {
  if (c.time_precision === "minute_as_reported" && c.event_time_utc) return `${fmtDay(c.event_time_utc.slice(0, 10))} ${new Date(c.event_time_utc).toISOString().slice(11, 16)}Z`;
  if (c.time_precision === "ambiguous_overnight") return "overnight 17–18 Aug (secondary reporting; conflicts with the official Aug 17 date — both kept)";
  return `${fmtDay(c.event_date) ?? "date unknown"} · time unknown`;
}
function pubTiming(s: CuratedSource) {
  if (s.publication_time_utc) return `published ${s.publication_time_utc.replace("T", " ").replace(/\+00:00$/, "Z")}`;
  if (s.publication_date) return `published ${s.publication_date}`;
  const asOf = s.as_of_date ?? s.retrieved_date;
  return asOf ? `publication date unknown, as of ${asOf}` : "publication date unknown";
}

/** Analyst-reviewed (manual) records for a replay scenario. Never correlated, never scored, no map markers. */
export function CuratedEvidence({ scenarioId, tMin, tMax, onSeek }: { scenarioId: string; tMin?: number; tMax?: number; onSeek: (t: number) => void }) {
  const evidence = useQuery({ queryKey: ["evidence", scenarioId], queryFn: () => api.evidence(scenarioId), retry: false, staleTime: Infinity });
  const [vessel, setVessel] = useState("all");
  const data = evidence.data;
  const sourcesById = useMemo(() => Object.fromEntries((data?.sources ?? []).map((s) => [s.id, s])), [data]);
  const vesselName = useMemo(() => Object.fromEntries((data?.vessels ?? []).map((v) => [v.id, v.name])), [data]);
  if (evidence.isError || !data) return null;                                  // scenario without curated records
  const win = { t_min: tMin ?? data.window?.t_min, t_max: tMax ?? data.window?.t_max };
  const winLabel = win.t_min && win.t_max ? `${new Date(win.t_min * 1000).toISOString().slice(0, 10)}${win.t_max - win.t_min > 90_000 ? " → " + new Date(win.t_max * 1000).toISOString().slice(0, 10) : ""}` : "?";
  const inWindow = (c: CuratedClaim) => { if (!c.event_time_utc || !win.t_min || !win.t_max) return false; const t = new Date(c.event_time_utc).getTime() / 1000; return t >= win.t_min && t <= win.t_max; };
  const claims = data.claims.filter((c) => vessel === "all" || c.vessel_id === vessel);

  return <Panel panelId="curated-evidence" className="max-h-[42vh] overflow-auto border-[#b45309]/60 p-3 scrollbar">
    <div className="flex items-center gap-2">
      <div className="mr-auto"><p className="font-mono text-[9px] font-bold uppercase tracking-[.18em] text-[#fbbf24]">Curated · manual</p><h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-muted">Curated evidence</h2></div>
      <select value={vessel} onChange={(e) => setVessel(e.target.value)} className="border border-line bg-black/30 px-1.5 py-0.5 font-mono text-[10px] text-ink">
        <option value="all">All vessels</option>{data.vessels.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </select>
    </div>
    <p className="mt-2 border border-[#92400e] bg-[#3b2a12] px-2 py-1 text-[10px] leading-snug text-[#fde68a]">Analyst-reviewed records compiled {data.prepared_date ?? "2026-09-08"} from reporting published after the events. Not sensor data. Not an as-known-at-the-time view. No threat score; never correlated with aircraft.</p>
    <ul className="mt-2 space-y-1 text-[10px] text-muted">{data.vessels.map((v) => <li key={v.id}><span className="font-semibold text-ink">{v.name}</span> · {v.vessel_type.replace(/_/g, " ")} · IMO {v.imo} · flag {v.flag} · MMSI candidate {v.mmsi_candidate ?? "–"} (unverified)</li>)}</ul>

    {claims.map((c) => {
      const srcs = c.source_ids.map((id) => sourcesById[id]).filter(Boolean);
      const seekable = inWindow(c);
      return <div key={c.id} className="mt-2 border border-dashed border-[#b45309] border-l-[3px] border-l-[#f59e0b] bg-[#161a14] p-2 text-xs">
        <div className="flex flex-wrap items-center gap-1">
          <Badge className="border-[#6d28d9]/60 bg-[#4c1d95]/60 text-[#ede9fe]">{c.evidence_class}</Badge>
          <Badge>{vesselName[c.vessel_id] ?? c.vessel_id}</Badge>
          <Badge className="normal-case tracking-normal">{claimTiming(c)}</Badge>
          {c.event_time_utc && (seekable
            ? <button className="ml-auto bg-command/90 px-2 py-0.5 font-mono text-[10px] uppercase text-black hover:bg-command" onClick={() => onSeek(new Date(c.event_time_utc!).getTime() / 1000)}>Seek</button>
            : <button disabled className="ml-auto cursor-not-allowed bg-black/30 px-2 py-0.5 font-mono text-[10px] text-muted" title="This instant is not inside the loaded replay window">outside replay window ({winLabel})</button>)}
        </div>
        <p className="mt-1 text-ink">{c.claim}</p>
        <p className="mt-1 text-[10px] text-muted">{c.location_text ?? "location not stated"} · coordinates unresolved · {c.attacker ? `attacker: ${c.attacker}` : "attacker: not attributed"}</p>
        <p className="mt-0.5 text-[10px] text-muted">{srcs.map((s, i) => <span key={s.id}>{i > 0 && " · "}<a className="text-command hover:underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.publisher}</a> ({s.source_type}; {pubTiming(s)})</span>)}</p>
      </div>;
    })}

    {data.leads.length > 0 && <>
      <p className="mt-3 font-mono text-[9px] font-bold uppercase tracking-[.18em] text-[#94a3b8]">Reviewed source leads</p>
      {data.leads.map((l) => {
        const later = /later context/i.test(l.status ?? "");
        return <div key={l.url} className="mt-1 border border-line border-l-[3px] border-l-[#94a3b8] bg-black/20 p-2 text-xs">
          <div className="flex flex-wrap gap-1"><Badge>{l.platform}</Badge><Badge>{l.original_language}</Badge><Badge className="normal-case tracking-normal">{l.publication_time_utc ?? "time unknown"}</Badge><Badge className="normal-case tracking-normal">{l.summary_kind ?? "paraphrase"}</Badge></div>
          <p className="mt-1 text-ink"><a className="text-command hover:underline" href={l.url} target="_blank" rel="noopener noreferrer">{l.publisher}</a> — {l.summary_en}</p>
          <p className="mt-0.5 text-[10px] text-muted">{l.status}{later && " · later context; vessel identity in the image not verified"}</p>
        </div>;
      })}
    </>}
    {data.excluded.length > 0 && <p className="mt-2 text-[10px] text-muted">Excluded: {data.excluded.map((x) => x.reason).join(" · ")}</p>}
  </Panel>;
}
