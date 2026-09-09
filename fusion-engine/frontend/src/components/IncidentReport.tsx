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

/** Full report for one incident: what happened in words, the numbers behind it, what could explain it, what to check next. */
export function IncidentReport({ inc, onClose, onFocus }: { inc: Incident | null; onClose: () => void; onFocus: (cell: [number, number]) => void }) {
  if (!inc) return <Panel className="p-3"><p className="font-mono text-[9px] font-bold uppercase tracking-[.18em] text-command">Incident report</p>
    <p className="mt-1 font-mono text-[10px] text-muted">Select an incident in the list or click a shaded cell on the map.</p></Panel>;
  const st = STATE[inc.state] ?? STATE.new_change;
  const a = inc.assessment;
  const rows = Object.entries(inc.streams).map(([k, s]) => ({ k, s }));
  return <Panel className="max-h-[70vh] overflow-auto p-3 scrollbar">
    <div className="flex items-start gap-2">
      <div className="mr-auto"><p className="font-mono text-[9px] font-bold uppercase tracking-[.18em] text-command">Incident report · {inc.id.replace("incident:", "#")}</p>
        <p className="mt-1 text-[12px] leading-snug text-ink">{a.headline ?? a.established}</p></div>
      <Badge className={st.cls}>{st.label}</Badge>
      <button onClick={onClose} className="border border-line px-1.5 font-mono text-[10px] text-muted hover:text-ink" aria-label="Close report">✕</button>
    </div>
    <p className="mt-1 font-mono text-[10px] text-muted">{a.place ? `${a.place} · ` : ""}{inc.cells.length} cell{inc.cells.length === 1 ? "" : "s"} · since {stamp(inc.first_t)} · last {stamp(inc.last_t)}
      {inc.cells[0] && <button className="ml-2 border border-line px-1 text-[9px] uppercase tracking-wider hover:border-command/60" onClick={() => onFocus([inc.cells[0][0] + .5, inc.cells[0][1] + .5])}>Show on map</button>}</p>

    <table className="mt-2 w-full font-mono text-[10px]"><thead><tr className="text-[9px] uppercase tracking-wider text-muted"><th className="text-left font-normal">Stream</th><th className="text-right font-normal">Now</th><th className="text-right font-normal">Normal</th><th className="text-right font-normal">z</th><th className="text-right font-normal">Basis</th><th className="text-left font-normal pl-2">State</th></tr></thead>
      <tbody>{rows.map(({ k, s }) => { const b = s.best; const isFrac = k === "navint";
        return <tr key={k} className={s.departed ? "text-ink" : "text-muted"}>
          <td className="py-0.5">{STREAM[k] ?? k}</td>
          <td className="text-right">{b ? (isFrac ? `${Math.round((b.value ?? 0) * 100)}%` : b.value) : "–"}</td>
          <td className="text-right">{b ? (isFrac ? `${Math.round((b.median ?? 0) * 100)}%` : b.median) : "–"}</td>
          <td className="text-right">{b?.z ?? "–"}</td>
          <td className="text-right">{b ? `${b.reference_n} ref${b.coverage != null ? ` · ${b.coverage} ac` : ""}` : (s.adequate ? "within ref" : "insufficient")}</td>
          <td className="pl-2">{s.departed ? (b?.state ?? "departed").replace("_", " ") : s.adequate ? "normal" : "insufficient coverage"}</td></tr>; })}</tbody></table>

    <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 font-mono text-[10px]">
      <dt className="uppercase tracking-wider text-muted">Established</dt><dd className="text-ink">{a.established}</dd>
      <dt className="uppercase tracking-wider text-muted">Disputed</dt><dd className="text-ink">{a.disputed}</dd>
      <dt className="uppercase tracking-wider text-muted">Unresolved</dt><dd className="text-ink">{a.unresolved}</dd>
      <dt className="uppercase tracking-wider text-muted">Relevance</dt><dd className="text-ink">{a.relevance}</dd>
    </dl>

    <p className="mb-1 mt-3 font-mono text-[9px] uppercase tracking-[.16em] text-muted">Explanations · each prediction tested against the streams{a.leading ? "" : " · none adequately supported"}</p>
    <div className="space-y-1.5">{inc.explanations.map((e, i) => <Explanation key={e.id} e={e} lead={i === 0 && !!a.leading} />)}</div>

    {inc.next_check ? <div className="mt-3 border border-command/40 bg-command/5 p-1.5">
      <p className="font-mono text-[9px] uppercase tracking-[.16em] text-command">Next check</p>
      <p className="font-mono text-[10px] text-ink">{predictionLabel(inc.next_check.prediction)}</p>
      <p className="font-mono text-[9px] text-muted">{inc.next_check.source} · {inc.next_check.why}</p></div>
      : <p className="mt-3 font-mono text-[9px] text-muted">No discriminating check remains.</p>}

    <p className="mb-1 mt-3 font-mono text-[9px] uppercase tracking-[.16em] text-muted">Revisions</p>
    <ul className="space-y-0.5 font-mono text-[10px]">{inc.revisions.slice(-8).reverse().map((r, i) => <li key={i} className="text-muted"><span className="text-ink">{stamp(r.t)}</span>
      {r.added.length > 0 && <span className="text-[#94c973]"> +{r.added.map((s) => (STREAM[s] ?? s).toLowerCase()).join(", ")}</span>}
      {r.gone.length > 0 && <span className="text-[#f0a09a]"> −{r.gone.map((s) => (STREAM[s] ?? s).toLowerCase()).join(", ")}</span>}
      {r.added.length === 0 && r.gone.length === 0 && <span> cells {r.cells}</span>}{r.leading && <span> · {r.leading}</span>}</li>)}</ul>
  </Panel>;
}
