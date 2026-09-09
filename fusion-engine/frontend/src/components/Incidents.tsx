import { useState } from "react";
import type { Incident, IncidentExplanation } from "../lib/types";
import { Panel } from "./ui/panel";
import { Badge } from "./ui/badge";

const pad2 = (v: number) => String(v).padStart(2, "0");
const stamp = (s: number) => { const d = new Date(s * 1000); return `${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}Z`; };
const STATE: Record<string, { label: string; cls: string }> = {
  new_change: { label: "new change", cls: "border-[#df5e55]/60 text-[#f0a09a]" },
  persistent: { label: "persistent", cls: "border-[#eab85a]/60 text-[#eab85a]" },
  recovering: { label: "recovering", cls: "border-[#5cc7da]/60 text-[#5cc7da]" },
};
const STREAM_LABEL: Record<string, string> = {
  news: "news", conflict: "conflict news", social: "social", tracks: "aircraft", military: "military air",
  firms_new: "thermal", navint: "nav integrity",
};
const STATUS_MARK: Record<string, string> = { supported: "✓", contradicted: "✗", untested: "?" };
const STATUS_CLS: Record<string, string> = { supported: "text-[#94c973]", contradicted: "text-[#f0a09a]", untested: "text-muted" };

function predictionLabel(p: string) {
  const [kind, arg] = p.split(":", 2);
  if (kind === "departed") return `${STREAM_LABEL[arg] ?? arg} departs from its hour-of-day reference`;
  if (kind === "quiet") return `${STREAM_LABEL[arg] ?? arg} stays within reference`;
  if (kind === "keywords") return `reporting mentions ${arg.split("|").slice(0, 3).join(", ")}…`;
  if (kind === "prior_day_same") return "same hour on the prior day was also elevated";
  if (kind === "coverage_drop") return "aircraft count fell well below reference (feed dip)";
  return p;
}

function ExplanationRow({ e, lead }: { e: IncidentExplanation; lead: boolean }) {
  const [open, setOpen] = useState(lead);
  return <div className={`border-l-2 pl-2 ${lead ? "border-command/70" : "border-line"}`}>
    <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left">
      <span className={`font-mono text-[11px] ${lead ? "text-ink" : "text-muted"}`}>{e.title}</span>
      <span className="ml-auto font-mono text-[10px] text-muted"><span className="text-[#94c973]">{e.supported}✓</span> <span className="text-[#f0a09a]">{e.contradicted}✗</span> <span>{e.untested}?</span></span>
    </button>
    {open && <ul className="mt-1 space-y-0.5">
      {e.predictions.map((p) => <li key={p.prediction} className="font-mono text-[10px] leading-snug">
        <span className={STATUS_CLS[p.status]}>{STATUS_MARK[p.status]} {predictionLabel(p.prediction)}</span>
        <span className="block pl-3 text-[9px] text-muted">{p.evidence}</span>
      </li>)}
    </ul>}
  </div>;
}

function IncidentCard({ inc, selected, onSelect, onFocus }: { inc: Incident; selected: boolean; onSelect: () => void; onFocus: (cell: [number, number]) => void }) {
  const st = STATE[inc.state] ?? STATE.new_change;
  const departed = Object.entries(inc.streams).filter(([, s]) => s.departed);
  const insufficient = Object.entries(inc.streams).filter(([, s]) => !s.adequate).map(([k]) => STREAM_LABEL[k] ?? k);
  return <div className={`border ${selected ? "border-command/60 bg-black/30" : "border-line bg-black/10"} p-2`}>
    <button onClick={onSelect} className="flex w-full items-center gap-2 text-left">
      <Badge className={st.cls}>{st.label}</Badge>
      <span className="font-mono text-[10px] text-muted">{inc.id.replace("incident:", "#")}</span>
      <span className="font-mono text-[10px] text-muted">since {stamp(inc.first_t)}</span>
      <span className="ml-auto font-mono text-[10px] text-ink">{inc.cells.length} cell{inc.cells.length === 1 ? "" : "s"}</span>
    </button>
    <div className="mt-1 flex flex-wrap gap-1">
      {departed.map(([k, s]) => <button key={k} title={s.best ? `z ${s.best.z} · value ${s.best.value} vs median ${s.best.median} · ${s.best.reference_n} reference values${s.best.coverage != null ? ` · ${s.best.coverage} aircraft` : ""}` : ""}
        onClick={() => s.best && onFocus([s.best.cell[0] + .5, s.best.cell[1] + .5])}
        className="border border-line bg-black/20 px-1 font-mono text-[9px] uppercase tracking-wider text-ink hover:border-command/60">
        {STREAM_LABEL[k] ?? k}{s.best?.z != null ? ` z${s.best.z}` : ""}</button>)}
      {insufficient.length > 0 && <span className="font-mono text-[9px] uppercase tracking-wider text-muted">insufficient: {insufficient.join(", ")}</span>}
    </div>
    <p className="mt-1 text-[11px] leading-snug text-ink">{inc.assessment.headline ?? inc.assessment.established}</p>
    {false && selected && <div className="mt-2 space-y-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 font-mono text-[10px]">
        <dt className="uppercase tracking-wider text-muted">Established</dt><dd className="text-ink">{inc.assessment.established}</dd>
        <dt className="uppercase tracking-wider text-muted">Disputed</dt><dd className="text-ink">{inc.assessment.disputed}</dd>
        <dt className="uppercase tracking-wider text-muted">Unresolved</dt><dd className="text-ink">{inc.assessment.unresolved}</dd>
        <dt className="uppercase tracking-wider text-muted">Relevance</dt><dd className="text-ink">{inc.assessment.relevance}</dd>
      </dl>
      <div>
        <p className="mb-1 font-mono text-[9px] uppercase tracking-[.16em] text-muted">Explanations · predictions tested against the streams</p>
        <div className="space-y-1">{inc.explanations.map((e, i) => <ExplanationRow key={e.id} e={e} lead={i === 0} />)}</div>
      </div>
      {inc.next_check ? <div className="border border-command/40 bg-command/5 p-1.5">
        <p className="font-mono text-[9px] uppercase tracking-[.16em] text-command">Next check</p>
        <p className="font-mono text-[10px] text-ink">{predictionLabel(inc.next_check.prediction)}</p>
        <p className="font-mono text-[9px] text-muted">{inc.next_check.source} · {inc.next_check.why}</p>
      </div> : <p className="font-mono text-[9px] text-muted">No discriminating check remains; every prediction is resolved.</p>}
      <div>
        <p className="mb-1 font-mono text-[9px] uppercase tracking-[.16em] text-muted">Revisions · what changed and when</p>
        <ul className="space-y-0.5 font-mono text-[10px]">
          {inc.revisions.slice(-6).reverse().map((r, i) => <li key={i} className="text-muted"><span className="text-ink">{stamp(r.t)}</span>
            {r.added.length > 0 && <span className="text-[#94c973]"> +{r.added.map((s) => STREAM_LABEL[s] ?? s).join(", ")}</span>}
            {r.gone.length > 0 && <span className="text-[#f0a09a]"> −{r.gone.map((s) => STREAM_LABEL[s] ?? s).join(", ")}</span>}
            {r.added.length === 0 && r.gone.length === 0 && <span> cells {r.cells}</span>}
            {r.leading && <span> · {r.leading}</span>}</li>)}
        </ul>
      </div>
    </div>}
  </div>;
}

/** Replay-mode incident queue: persistent objects formed from cells that departed their hour-of-day reference. */
export type IncidentsMeta = { status?: string; built_at?: string | null; assessed_through?: string | null; aircraft_history?: { from: string; to: string } | null; mode: "live" | "replay" };
const hhmm = (iso?: string | null) => iso ? `${iso.slice(11, 16)}Z` : null;
export function Incidents({ incidents, baseline, status, meta, onFocus, selected: selectedProp, onSelectChange }: { incidents: Incident[]; baseline?: { z_threshold: number; reference: string; days: number; note?: string }; status?: string; meta?: IncidentsMeta; onFocus: (cell: [number, number]) => void; selected?: string | null; onSelectChange?: (id: string | null) => void }) {
  const [internal, setInternal] = useState<string | null>(null);
  const selected = selectedProp !== undefined ? selectedProp : internal;
  const setSelected = (id: string | null) => { setInternal(id); onSelectChange?.(id); };
  const ordered = [...incidents].sort((a, b) => {
    const rank = (x: Incident) => (x.state === "recovering" ? 2 : x.state === "persistent" ? 1 : 0);
    const multi = (x: Incident) => Object.values(x.streams).filter((s) => s.departed).length;
    return rank(a) - rank(b) || multi(b) - multi(a);
  });
  return <Panel className="max-h-[46vh] overflow-auto p-3 scrollbar">
    <div className="mb-2 flex items-baseline gap-2">
      <p className="font-mono text-[9px] font-bold uppercase tracking-[.18em] text-command">Incidents · machine</p>
      <h2 className="font-mono text-[10px] font-semibold uppercase tracking-[.14em] text-ink">{incidents.length} at this instant</h2>
      {baseline && <span className="ml-auto font-mono text-[9px] text-muted" title={baseline.reference}>z ≥ {baseline.z_threshold} · {baseline.days}-day reference</span>}
    </div>
    {meta && <div className={`mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border px-2 py-1 font-mono text-[9px] ${meta.status && meta.status !== "done" ? "border-[#eab85a]/50 text-[#eab85a]" : "border-line text-muted"}`}>
      {meta.status && meta.status !== "done" ? <span className="animate-pulse">● {meta.status.startsWith("failed") ? meta.status : `data loading · ${meta.status}`}</span> : <span className="text-[#94c973]">● analysis current</span>}
      {meta.assessed_through && <span>assessed through {hhmm(meta.assessed_through)}</span>}
      {meta.built_at && <span>built {hhmm(meta.built_at)}{meta.mode === "live" ? ` · next refresh ${hhmm(new Date(new Date(meta.built_at).getTime() + 15 * 60_000).toISOString())}` : ""}</span>}
      {meta.mode === "live" && <span title="Military and navigation-integrity streams have a reference only inside this span; earlier bins read insufficient">aircraft history {meta.aircraft_history ? `${hhmm(meta.aircraft_history.from)} → ${hhmm(meta.aircraft_history.to)}` : "none yet"}</span>}
    </div>}
    {incidents.length === 0 ? <p className="font-mono text-[10px] text-muted">{status && status !== "done" ? `Data loading: ${status}. Incidents appear once the 48-hour history is in.` : "No stream is departed from its hour-of-day reference in any cell."}</p>
      : <div className="space-y-1.5">{ordered.map((inc) => <IncidentCard key={inc.id} inc={inc} selected={selected === inc.id} onSelect={() => setSelected(selected === inc.id ? null : inc.id)} onFocus={onFocus} />)}</div>}
    <p className="mt-2 font-mono text-[9px] leading-snug text-muted">Formed from evidence available at the displayed instant only. Explanations are tested, not scored: each prediction is supported, contradicted, or untested. Reference is thin ({baseline?.days ?? 2} days) and every figure carries the count it rests on.</p>
  </Panel>;
}
