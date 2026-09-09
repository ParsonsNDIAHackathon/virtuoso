import { X } from "lucide-react";
import type { AoiSummary, Region } from "../lib/types";
import { Button } from "./ui/button";

export function AoiSummaryPanel({ region, result, pending, error, onClose, onRetry }: {
  region: Region; result?: AoiSummary; pending: boolean; error: Error | null;
  onClose: () => void; onRetry: () => void;
}) {
  const sourceNumber = (key: string) => (result?.sources.findIndex((s) => s.key === key) ?? -1) + 1;
  return <section aria-label="AOI AI summary" aria-busy={pending} className="absolute bottom-10 left-3 top-3 z-[1000] flex w-[min(28rem,calc(100%-1.5rem))] flex-col overflow-hidden border border-command/50 bg-panel/95 shadow-xl">
    <header className="flex items-center gap-3 border-b border-line px-3 py-2">
      <div className="min-w-0"><p className="font-mono text-[10px] uppercase text-command">AI area summary</p><h2 className="break-words text-sm font-semibold text-ink">{region.name}</h2><p className="text-[10px] text-muted">{region.radius_nm} nm radius · all available sources</p></div>
      <button className="ml-auto shrink-0 p-1 text-muted hover:text-ink" aria-label="Close AOI summary" onClick={onClose}><X size={16} /></button>
    </header>
    <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-xs leading-relaxed scrollbar" aria-live="polite">
      {pending && <p className="text-muted">Analyzing all available records inside this circle… Larger areas may take a few minutes.</p>}
      {error && <div role="alert"><p className="mb-3 text-critical">{error.message}</p><Button size="sm" onClick={onRetry}>Retry analysis</Button></div>}
      {result && <>
        <p className="text-[10px] text-muted">{result.total_records.toLocaleString()} records · {result.mode === "live" ? "Live snapshot" : "Replay"} · {new Date(result.as_of).toLocaleString()}{result.cached ? " · cached analysis" : ""}</p>
        <p className="font-mono text-[10px] text-command">{Object.entries(result.counts).map(([kind, count]) => `${kind.toUpperCase()} ${count.toLocaleString()}`).join(" · ")}</p>
        {result.record_time_start && result.record_time_end && <p className="text-[10px] text-muted">Record times: {new Date(result.record_time_start).toLocaleString()} – {new Date(result.record_time_end).toLocaleString()}</p>}
        <p className="whitespace-pre-wrap text-ink">{result.summary}</p>
        {result.findings.map((finding, i) => <p key={i} className="border-l-2 border-command/40 pl-2 text-ink">{finding.text}{" "}{finding.record_ids.map((key) => <a key={key} href={`#aoi-source-${sourceNumber(key)}`} className="ml-1 text-command" title="Jump to cited source">[{sourceNumber(key)}]</a>)}</p>)}
        <div className="space-y-1 border-t border-line pt-2"><h3 className="font-semibold text-muted">Evidence gaps</h3>{result.caveats.map((caveat, i) => <p key={i} className="text-muted">{caveat}</p>)}</div>
        {result.sources.length > 0 && <div className="space-y-2 border-t border-line pt-2"><h3 className="font-semibold text-muted">Sources</h3>{result.sources.map((source, i) => <div id={`aoi-source-${i + 1}`} key={source.key} className="break-words text-[10px] text-muted"><p>[{i + 1}] {source.kind.toUpperCase()} · {source.label}</p><p>{new Date(source.ts).toLocaleString()} · {source.lat.toFixed(4)}, {source.lon.toFixed(4)}</p>{source.url && /^https?:\/\//i.test(source.url) && <a className="text-command underline" href={source.url} target="_blank" rel="noreferrer">Open source</a>}</div>)}</div>}
      </>}
    </div>
  </section>;
}
