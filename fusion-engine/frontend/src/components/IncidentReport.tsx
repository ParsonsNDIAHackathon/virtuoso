import type { Incident, IncidentEvidence, IncidentExplanation } from "../lib/types";
import { Panel } from "./ui/panel";
import { Badge } from "./ui/badge";

const pad2 = (v: number) => String(v).padStart(2, "0");
const stamp = (s: number) => { const d = new Date(s * 1000); return `${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}Z`; };
const hhmm = (s: number) => { const d = new Date(s * 1000); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}Z`; };
const STATE: Record<string, { label: string; cls: string }> = {
  new_change: { label: "new change", cls: "border-[#df5e55]/60 text-[#f0a09a]" },
  persistent: { label: "persistent", cls: "border-[#eab85a]/60 text-[#eab85a]" },
  recovering: { label: "recovering", cls: "border-[#5cc7da]/60 text-[#5cc7da]" },
};
const STREAM: Record<string, string> = { news: "News articles", conflict: "Conflict-coded articles", social: "Social posts", tracks: "Aircraft (coverage)", military: "Military aircraft", firms_new: "New thermal detections", navint: "Degraded nav integrity" };
const MARK: Record<string, string> = { supported: "✓", contradicted: "✗", untested: "?" };
const MARK_CLS: Record<string, string> = { supported: "text-[#94c973]", contradicted: "text-[#f0a09a]", untested: "text-muted" };

function predictionLabel(p: string) {
  const [kind, arg] = p.split(":", 2);
  const name = (k: string) => (STREAM[k] ?? k).toLowerCase();
  if (kind === "departed") return `${name(arg)} depart from the hour-of-day reference`;
  if (kind === "quiet") return `${name(arg)} stay within reference`;
  if (kind === "keywords") return `reporting mentions ${arg.split("|").slice(0, 3).join(", ")}…`;
  if (kind === "prior_day_same") return "same hour on the prior day was also elevated";
  if (kind === "coverage_drop") return "aircraft count fell well below reference (feed dip)";
  return p;
}

function Explanation({ e, lead }: { e: IncidentExplanation; lead: boolean }) {
  return <div className={`border-l-2 pl-2 ${lead ? "border-command/70" : "border-line"}`}>
    <div className="flex items-center gap-2"><span className={`font-mono text-[11px] ${lead ? "text-ink" : "text-muted"}`}>{e.title}</span>
      <span className="ml-auto font-mono text-[10px]"><span className="text-[#94c973]">{e.supported}✓</span> <span className="text-[#f0a09a]">{e.contradicted}✗</span> <span className="text-muted">{e.untested}?</span></span></div>
    <ul className="mt-1 space-y-0.5">{e.predictions.map((p) => <li key={p.prediction} className="font-mono text-[10px] leading-snug">
      <span className={MARK_CLS[p.status]}>{MARK[p.status]} {predictionLabel(p.prediction)}</span><span className="block pl-3 text-[9px] text-muted">{p.evidence}</span></li>)}</ul>
  </div>;
}

const H = ({ children }: { children: React.ReactNode }) => <p className="mb-1 mt-3 font-mono text-[9px] uppercase tracking-[.16em] text-muted">{children}</p>;

/** Full report for one incident, in reading order: what changed → the records behind it → what it means
 *  operationally → what is still open → what to do next. The hypothesis checklist and the per-stream
 *  statistics sit underneath for the analyst who wants the numbers. */
export function IncidentReport({ inc, onClose, onFocus, onEvidence }: { inc: Incident | null; onClose: () => void; onFocus: (cell: [number, number]) => void; onEvidence?: (rec: IncidentEvidence) => void }) {
  if (!inc) return <Panel className="p-3"><p className="font-mono text-[9px] font-bold uppercase tracking-[.18em] text-command">Incident report</p>
    <p className="mt-1 font-mono text-[10px] text-muted">Select an incident in the list or click a shaded cell on the map.</p></Panel>;
  const st = STATE[inc.state] ?? STATE.new_change;
  const a = inc.assessment;
  const rows = Object.entries(inc.streams).map(([k, s]) => ({ k, s }));
  const evidence = inc.evidence ?? [];
  const act = a.next_action;
  return <Panel openOn={inc.id} className="max-h-[70vh] overflow-auto p-3 scrollbar">
    <div className="flex items-start gap-2">
      <div className="mr-auto"><p className="font-mono text-[9px] font-bold uppercase tracking-[.18em] text-command">Incident report · {inc.id.replace("incident:", "#")}</p>
        <p className="mt-1 text-[12px] leading-snug text-ink">{a.headline ?? a.established}</p></div>
      <Badge className={st.cls}>{st.label}</Badge>
      <button onClick={onClose} className="border border-line px-1.5 font-mono text-[10px] text-muted hover:text-ink" aria-label="Close report">✕</button>
    </div>
    <p className="mt-1 font-mono text-[10px] text-muted">{a.place ? `${a.place} · ` : ""}{inc.cells.length} cell{inc.cells.length === 1 ? "" : "s"} · since {stamp(inc.first_t)} · last {stamp(inc.last_t)}
      {inc.cells[0] && <button className="ml-2 border border-line px-1 text-[9px] uppercase tracking-wider hover:border-command/60" onClick={() => onFocus([inc.cells[0][0] + .5, inc.cells[0][1] + .5])}>Show on map</button>}</p>

    {/* 1. what changed: counts before statistics */}
    <H>What changed</H>
    {(a.changes ?? []).length > 0
      ? <ul className="space-y-0.5 font-mono text-[10px] text-ink">{(a.changes ?? []).map((c) => <li key={c}>• {c}</li>)}</ul>
      : <p className="font-mono text-[10px] text-muted">{a.established}</p>}

    {/* 2. the records behind the counts */}
    <H>Evidence · {evidence.length ? `${(inc.evidence_total ?? evidence.length) > evidence.length ? `sample of ${evidence.length} from ` : ""}${inc.evidence_total ?? evidence.length} article${(inc.evidence_total ?? evidence.length) === 1 ? "" : "s"}/posts this hour · ${inc.evidence_scope || "incident cells"}` : "no article or post records in the cells this hour"}</H>
    {evidence.length > 0 && <ul className="space-y-0.5">{evidence.map((r) => <li key={r.id} className="flex items-baseline gap-1 font-mono text-[10px]">
      <span className="shrink-0 text-[9px] uppercase tracking-wider text-muted">{r.stream === "social" ? r.kind : r.stream}</span>
      <button className="min-w-0 flex-1 truncate text-left text-ink hover:text-command" title={`${r.text || r.title}${r.neighbour ? " · neighbouring cell" : ""}`} onClick={() => onEvidence?.(r)}>{r.title}{r.place ? ` · ${r.place}` : ""}{(r.mentions ?? 1) > 1 ? ` · ${r.mentions} coded events` : ""}{r.neighbour ? " · nbr" : ""}</button>
      <span className="shrink-0 text-[9px] text-muted">{hhmm(Date.parse(r.ts) / 1000)}</span>
      {r.url && <a className="shrink-0 text-[9px] uppercase tracking-wider text-muted hover:text-command" href={r.url} target="_blank" rel="noreferrer">open</a>}
    </li>)}</ul>}
    {evidence.length === 0 && (a.changes ?? []).length > 0 && <p className="font-mono text-[9px] text-muted">The departed streams are measurements (aircraft, thermal, integrity); their counts are in the table below.</p>}

    {/* 3. operational significance */}
    <H>Operational significance</H>
    <p className="font-mono text-[10px] leading-snug text-ink">{a.operational_impact ?? a.relevance}</p>

    {/* 4. what is still open */}
    <H>Unresolved</H>
    <p className="font-mono text-[10px] leading-snug text-ink">{a.question ?? a.unresolved}</p>
    <p className="font-mono text-[9px] leading-snug text-muted">Coverage: {a.unresolved}</p>
    <p className="font-mono text-[9px] leading-snug text-muted">Explanations: {a.disputed}</p>

    {/* 5. the next action */}
    {act ? <div className="mt-3 border border-command/40 bg-command/5 p-1.5">
      <p className="font-mono text-[9px] uppercase tracking-[.16em] text-command">Next action · {act.mode === "analyst" ? "analyst task" : "scheduled recheck"}</p>
      <p className="font-mono text-[10px] text-ink">{act.question}</p>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 font-mono text-[9px]">
        <dt className="uppercase tracking-wider text-muted">Source</dt><dd className="text-ink">{act.source}</dd>
        <dt className="uppercase tracking-wider text-muted">Area</dt><dd className="text-ink">{act.area}</dd>
        <dt className="uppercase tracking-wider text-muted">Window</dt><dd className="text-ink">{stamp(act.window[0])} – {hhmm(act.window[1])}</dd>
        <dt className="uppercase tracking-wider text-muted">How</dt><dd className="text-muted">{act.how}</dd>
        <dt className="uppercase tracking-wider text-muted">Why</dt><dd className="text-muted">{act.why}</dd>
      </dl></div>
      : inc.next_check ? <div className="mt-3 border border-command/40 bg-command/5 p-1.5">
        <p className="font-mono text-[9px] uppercase tracking-[.16em] text-command">Next check</p>
        <p className="font-mono text-[10px] text-ink">{predictionLabel(inc.next_check.prediction)}</p>
        <p className="font-mono text-[9px] text-muted">{inc.next_check.source} · {inc.next_check.why}</p></div>
        : <p className="mt-3 font-mono text-[9px] text-muted">No discriminating check remains.</p>}

    {/* underneath: the numbers and the hypothesis checklist */}
    <details className="mt-3" open>
      <summary className="cursor-pointer font-mono text-[9px] uppercase tracking-[.16em] text-muted">Streams · observed vs reference, and why a stream has no verdict</summary>
      <table className="mt-1 w-full font-mono text-[10px]"><thead><tr className="text-[9px] uppercase tracking-wider text-muted"><th className="text-left font-normal">Stream</th><th className="text-right font-normal">Now</th><th className="text-right font-normal">Normal</th><th className="text-right font-normal">z</th><th className="text-right font-normal">Basis</th><th className="text-left font-normal pl-2">Verdict</th></tr></thead>
        <tbody>{rows.map(({ k, s }) => { const b = s.best ?? s.detail ?? null; const isFrac = k === "navint"; const insufficient = !s.adequate;
          return <tr key={k} className={s.departed ? "text-ink" : "text-muted"}>
            <td className="py-0.5">{STREAM[k] ?? k}</td>
            <td className="text-right">{!b ? "–" : isFrac ? ((b.coverage ?? 0) >= 15 && !insufficient ? `${Math.round((b.value ?? 0) * 100)}%` : "unavailable") : b.value}</td>
            <td className="text-right">{b && !insufficient ? (isFrac ? `${Math.round((b.median ?? 0) * 100)}%` : b.median) : "–"}</td>
            <td className="text-right">{b?.z ?? "–"}</td>
            <td className="text-right">{b ? `${b.reference_n} ref${b.coverage != null ? ` · ${b.coverage} ac` : ""}` : "–"}</td>
            <td className="pl-2" title={s.reason}>{s.departed ? (b?.state ?? "departed").replace("_", " ") : s.adequate ? "normal" : `insufficient · ${s.reason ?? "coverage"}`}</td></tr>; })}</tbody></table>
    </details>

    <details className="mt-2">
      <summary className="cursor-pointer font-mono text-[9px] uppercase tracking-[.16em] text-muted">Explanations · each prediction tested against the streams{a.leading ? ` · leading: ${a.leading}` : " · none adequately supported"}</summary>
      <div className="mt-1 space-y-1.5">{inc.explanations.map((e, i) => <Explanation key={e.id} e={e} lead={i === 0 && !!a.leading} />)}</div>
      <p className="mt-1 font-mono text-[9px] text-muted">"?" means the prediction could not be tested this hour (no coverage), not that it failed.</p>
    </details>

    <details className="mt-2">
      <summary className="cursor-pointer font-mono text-[9px] uppercase tracking-[.16em] text-muted">Revisions · {inc.revisions.length}</summary>
      <ul className="mt-1 space-y-0.5 font-mono text-[10px]">{inc.revisions.slice(-8).reverse().map((r, i) => <li key={i} className="text-muted"><span className="text-ink">{stamp(r.t)}</span>
        {r.added.length > 0 && <span className="text-[#94c973]"> +{r.added.map((s) => (STREAM[s] ?? s).toLowerCase()).join(", ")}</span>}
        {r.gone.length > 0 && <span className="text-[#f0a09a]"> −{r.gone.map((s) => (STREAM[s] ?? s).toLowerCase()).join(", ")}</span>}
        {r.added.length === 0 && r.gone.length === 0 && <span> cells {r.cells}</span>}{r.leading && <span> · {r.leading}</span>}</li>)}</ul>
    </details>
  </Panel>;
}
