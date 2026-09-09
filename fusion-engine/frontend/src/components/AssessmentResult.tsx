import type { Assessment } from "../lib/types";

const incidentLabels: Record<string, string> = {
  SAME_INCIDENT: "Same incident", RELATED_INCIDENTS: "Related incidents",
  UNRELATED: "Unrelated incidents", UNCERTAIN: "Uncertain",
};

export function AssessmentResult({ assessment, compact = false }: { assessment: Assessment; compact?: boolean }) {
  const sameArticle = assessment.article_match?.status === "SAME_ARTICLE";
  const newsPair = [assessment.left_kind, assessment.right_kind].every((kind) => ["gdelt", "telegram"].includes(kind));
  const label = newsPair ? incidentLabels[assessment.incident_relationship ?? "UNCERTAIN"] : assessment.relation.replace(/_/g, " ");
  const tone = assessment.verdict === "SUPPORTED" ? "text-command" : assessment.verdict === "CONTRADICTED" ? "text-critical" : "text-[#eab85a]";
  return <div className="space-y-1.5 text-[10px]">
    {sameArticle && <div className="border-l-2 border-command bg-command/5 py-1 pl-2">
      <p className="font-semibold text-command">Same article · Confirmed <span className="float-right pr-1 font-mono">Match {assessment.article_match?.confidence?.toFixed(2)}</span></p>
      <p className="text-muted">1 shared reporting source · no added independent corroboration</p>
      {!compact && <p className="text-muted">{assessment.article_match?.basis === "redirect_url" ? "Source URLs resolve to the same article." : "Article URLs match after removing tracking parameters."}</p>}
    </div>}
    {!compact && assessment.article_match?.status === "NOT_ESTABLISHED" && <p className="text-muted">Article match: not established · source independence unverified</p>}
    <div className="flex items-center gap-2">
      <p className={`font-semibold ${tone}`}>{newsPair ? "Incident link" : "Evidence link"} · {label}</p>
      <span className="ml-auto font-mono text-muted" title="Strength of evidence for the incident or observation link, separate from article identity.">Strength {assessment.evidence_strength.toFixed(2)}</span>
    </div>
    <p className="text-muted">{assessment.verdict.replace(/_/g, " ")}{assessment.needs_review ? " · Needs review" : ""} · {assessment.cached ? "cached" : assessment.model}</p>
    <p className="leading-snug text-ink">{assessment.rationale}</p>
    {!compact && <>
      <p className="text-muted">Limitation: {assessment.strongest_limitation}</p>
      {(assessment.source_documents ?? []).map((doc) => <div key={doc.record_id} className="border-t border-line pt-1" title={doc.title ?? doc.url}>
        <p className={doc.available ? "text-command" : "text-[#eab85a]"}>Article {doc.record_id === assessment.left_id ? "A" : "B"}: {doc.available ? `${doc.characters.toLocaleString()} characters loaded${doc.truncated ? " · truncated" : ""}` : "text unavailable"}</p>
        {!doc.available && <p className="text-muted">{doc.limitation}</p>}
      </div>)}
      {assessment.supporting_facts.length > 0 && <details className="text-muted"><summary className="cursor-pointer">Supporting evidence</summary><ul className="mt-1 list-disc space-y-1 pl-4">{assessment.supporting_facts.map((fact, i) => <li key={i}>{fact}</li>)}</ul></details>}
    </>}
  </div>;
}
