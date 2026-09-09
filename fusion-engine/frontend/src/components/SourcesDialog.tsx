import { X } from "lucide-react";
import { Button } from "./ui/button";

const sources = [
  ["OSINT event", "GDELT Project", "Worldwide news events, actors, tone, and geocoded locations.", "Every 15 minutes"],
  ["Telegram post", "Telegram public channels", "Public-preview posts that name a known location.", "Every 5 minutes"],
  ["AIS vessels", "aisstream.io", "Live vessel reports within AOI circles. Receiver coverage varies; reporting gaps do not prove a vessel stopped transmitting. No historical replay.", "Streaming · view refreshes every 10 seconds"],
  ["Aircraft", "adsb.lol", "Cooperative ADS-B transponder positions; coverage varies by receiver density.", "Every 60 seconds"],
  ["Thermal anomaly", "NASA FIRMS / VIIRS", "Satellite heat detections. Bright/new signals are absent from the two-day baseline.", "Every 15 minutes"],
  ["Satellite imagery", "NASA GIBS", "VIIRS true-colour basemap. It is context only, not a detection source.", "Daily"],
  ["RF sample", "Synthetic demo", "One sample near the Strait of Hormuz with a simulated 60-second chirp spectrogram; not a live measurement.", "Static sample"],
  ["Candidate retrieval", "Fusion engine", "Pair-specific space/time and source-entity retrieval. A candidate is an analyst cue, not a finding.", "Each fusion cycle"],
  ["Article identity", "Fusion engine", "Matching article URLs identify shared reporting. Multiple GDELT events from one article count as one reporting source.", "On comparison"],
  ["Evidence assessment", "OpenAI", "Separate incident/evidence verdict using source fields, graph facts, and GDELT article text. Plausible links require review; purple dashed links can indicate shared articles with uncertain incident links.", "Cached + on demand"],
] as const;

export function SourcesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return <div className="fixed inset-0 z-[2000] grid place-items-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Data sources" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section className="max-h-[85vh] w-full max-w-5xl overflow-auto border border-line bg-panel p-4 shadow-panel scrollbar"><header className="mb-3 flex items-start gap-4"><div><h2 className="font-mono text-sm font-bold uppercase tracking-wider">Data sources</h2><p className="mt-1 text-xs text-muted">Every marker is sourced from the organization shown below. Live mode streams current data; replay uses historical source data.</p></div><Button className="ml-auto" size="sm" variant="ghost" onClick={onClose} aria-label="Close sources"><X size={15} /></Button></header><table className="w-full min-w-[700px] text-left text-xs"><thead className="sticky top-0 bg-panel font-mono text-[10px] uppercase tracking-wide text-muted"><tr><th className="p-2">Layer</th><th className="p-2">Producer</th><th className="p-2">What it means</th><th className="p-2">Cadence</th></tr></thead><tbody>{sources.map(([layer, producer, meaning, cadence]) => <tr key={layer} className="border-t border-line/70 align-top"><td className="p-2 font-semibold text-ink">{layer}</td><td className="p-2 text-command">{producer}</td><td className="p-2 text-muted">{meaning}</td><td className="p-2 font-mono text-muted">{cadence}</td></tr>)}</tbody></table></section></div>;
}
