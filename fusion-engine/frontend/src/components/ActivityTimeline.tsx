import { useEffect, useRef, useState } from "react";
import type { Timeline, TimelineBin } from "../lib/types";

type SeriesKey = "conflict" | "social" | "tracks" | "military" | "firms_new" | "alerts";
type Series = { key: SeriesKey; label: string; color: string };

// Replay: arrivals per 15-min bin across the scenario day.
const REPLAY_SERIES: Series[] = [
  { key: "conflict", label: "conflict OSINT events", color: "#eab85a" },
  { key: "social", label: "Telegram posts", color: "#d99add" },
  { key: "tracks", label: "aircraft", color: "#5cc7da" },
  { key: "military", label: "military aircraft", color: "#df5e55" },
  { key: "firms_new", label: "new thermal anomalies", color: "#ef4444" },
  { key: "alerts", label: "correlations", color: "#a78bfa" },
];
// Live: 15-min bins inside the drawn circles; GDELT + Telegram backfilled from the sources,
// aircraft as levels and correlations as flows from the server's own history.
const LIVE_SERIES: Series[] = [
  { key: "conflict", label: "conflict OSINT events (in circles)", color: "#eab85a" },
  { key: "social", label: "Telegram posts (in circles)", color: "#d99add" },
  { key: "tracks", label: "aircraft in coverage", color: "#5cc7da" },
  { key: "military", label: "military aircraft", color: "#df5e55" },
  { key: "firms_new", label: "new thermal anomalies", color: "#ef4444" },
  { key: "alerts", label: "new correlations", color: "#a78bfa" },
];
const MARGIN = { left: 44, right: 14, top: 8, bottom: 22 };
const pad2 = (value: number) => String(value).padStart(2, "0");
const stampUtc = (seconds: number, withDate: boolean) => {
  const d = new Date(seconds * 1000);
  const time = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}Z`;
  return withDate ? `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${time}` : time;
};
const valueOf = (bin: TimelineBin, key: SeriesKey): number | null => bin[key] ?? null;

type Props = { timeline?: Timeline; activeTime?: number | null; mode: "live" | "replay"; onSeek?: (time: number) => void };

/** Small-multiple activity strip: one row per source, each normalised to its own maximum so a
 *  1,500-aircraft level never flattens 20 posts. Hover shows the real counts; click seeks replay. */
export function ActivityTimeline({ timeline, activeTime, mode, onSeek }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<{ x: number; y: number; bin: TimelineBin } | null>(null);
  const bins = timeline?.bins ?? [];
  const step = (timeline?.step_min ?? 15) * 60;
  const allSeries = mode === "replay" ? REPLAY_SERIES : LIVE_SERIES;
  const series = mode === "replay" ? allSeries.filter((s) => bins.some((bin) => (valueOf(bin, s.key) ?? 0) > 0)) : allSeries;
  const start = bins.length ? bins[0].t : 0;
  const end = bins.length ? bins[bins.length - 1].t + step : 1;

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => {
      const rect = element.getBoundingClientRect(); const ratio = window.devicePixelRatio || 1;
      element.width = Math.max(1, Math.floor(rect.width * ratio)); element.height = Math.max(1, Math.floor(rect.height * ratio));
      const context = element.getContext("2d"); if (!context) return;
      context.scale(ratio, ratio); context.clearRect(0, 0, rect.width, rect.height);
      context.font = "10px Geist Mono, ui-monospace, monospace";
      if (!bins.length || !series.length) {
        context.fillStyle = "#8a9885";
        context.fillText(mode === "live" ? "No timeline points yet (one per fuse, every 60 s; history persists across restarts)." : "No activity in this scenario.", MARGIN.left, 16);
        return;
      }
      const width = rect.width - MARGIN.left - MARGIN.right; const height = rect.height - MARGIN.top - MARGIN.bottom;
      const x = (t: number) => MARGIN.left + (t - start) / (end - start) * width;
      const rowHeight = height / series.length;
      series.forEach((s, row) => {
        const y0 = MARGIN.top + row * rowHeight; const base = y0 + rowHeight - 2;
        const max = Math.max(1, ...bins.map((bin) => valueOf(bin, s.key) ?? 0));
        const y = (v: number) => base - (v / max) * (rowHeight - 6);
        context.strokeStyle = "rgba(105, 123, 100, .35)"; context.lineWidth = 1;
        context.beginPath(); context.moveTo(MARGIN.left, base); context.lineTo(rect.width - MARGIN.right, base); context.stroke();
        context.fillStyle = s.color; context.textAlign = "right"; context.fillText(String(max), MARGIN.left - 6, y0 + rowHeight / 2 + 4); context.textAlign = "left";
        // Area fill, broken wherever a level is null (nothing recorded yet).
        context.beginPath(); let open = false;
        bins.forEach((bin) => {
          const v = valueOf(bin, s.key); const px = x(bin.t + step / 2);
          if (v === null) { if (open) { context.lineTo(x(bin.t), base); context.closePath(); open = false; } return; }
          if (!open) { context.moveTo(px, base); open = true; }
          context.lineTo(px, y(v));
        });
        if (open) { context.lineTo(x(end), base); context.closePath(); }
        context.fillStyle = s.color; context.globalAlpha = .18; context.fill(); context.globalAlpha = 1;
        // Outline.
        context.strokeStyle = s.color; context.lineWidth = 1.2; context.beginPath(); open = false;
        bins.forEach((bin) => {
          const v = valueOf(bin, s.key);
          if (v === null) { open = false; return; }
          const px = x(bin.t + step / 2);
          if (!open) { context.moveTo(px, y(v)); open = true; } else context.lineTo(px, y(v));
        });
        context.stroke();
        // Point markers, so a short recorded span reads as points rather than a sliver.
        if (bins.length < 200) bins.forEach((bin) => { const v = valueOf(bin, s.key); if (v === null) return; context.beginPath(); context.arc(x(bin.t + step / 2), y(v), 1.8, 0, Math.PI * 2); context.fill(); });
      });
      // Time axis.
      const spanHours = (end - start) / 3600; const ticks = 6;
      context.fillStyle = "#8a9885"; context.strokeStyle = "rgba(138, 152, 133, .4)"; context.lineWidth = 1;
      for (let i = 0; i <= ticks; i += 1) {
        const t = start + (end - start) * i / ticks; const px = x(t);
        context.beginPath(); context.moveTo(px, MARGIN.top + height); context.lineTo(px, MARGIN.top + height + 4); context.stroke();
        context.textAlign = i === 0 ? "left" : i === ticks ? "right" : "center"; context.fillText(stampUtc(t, spanHours > 36), px, rect.height - 6);
      }
      context.textAlign = "left";
      // Radar scene markers (replay).
      for (const scene of timeline?.sar_scenes ?? []) {
        if (scene.t < start || scene.t > end) continue;
        const px = x(scene.t);
        context.strokeStyle = "#e5e7df"; context.setLineDash([3, 3]); context.beginPath(); context.moveTo(px, MARGIN.top); context.lineTo(px, MARGIN.top + height); context.stroke(); context.setLineDash([]);
        context.fillStyle = "#e5e7df"; context.fillText(`radar ${scene.n} ships`, px + 3, MARGIN.top + height - 4);
      }
      // Scrubber cursor (replay).
      if (activeTime && activeTime >= start && activeTime <= end) {
        const px = x(activeTime);
        context.strokeStyle = "#ffffff"; context.lineWidth = 1.5; context.beginPath(); context.moveTo(px, MARGIN.top); context.lineTo(px, MARGIN.top + height); context.stroke();
      }
    };
    draw(); const observer = new ResizeObserver(draw); observer.observe(element); return () => observer.disconnect();
  }, [timeline, activeTime, mode, bins, series, step, start, end]);

  const timeAt = (clientX: number) => {
    const element = canvas.current; if (!element || !bins.length) return null;
    const rect = element.getBoundingClientRect(); const width = rect.width - MARGIN.left - MARGIN.right;
    return start + Math.max(0, Math.min(1, (clientX - rect.left - MARGIN.left) / width)) * (end - start);
  };
  const nearest = (t: number) => bins.reduce((best, bin) => Math.abs(bin.t + step / 2 - t) < Math.abs(best.t + step / 2 - t) ? bin : best, bins[0]);
  const backfill = mode === "live" ? timeline?.backfill : undefined;

  return <div className="relative flex h-full min-h-[200px] flex-col p-3">
    <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1">
      <h2 className="mr-2 font-mono text-[10px] uppercase tracking-[.14em] text-muted">Multi-source activity timeline</h2>
      {series.map((s) => <span key={s.key} className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-wide text-muted"><i className="h-0.5 w-3" style={{ background: s.color }} />{s.label}</span>)}
      {backfill && <span className="font-mono text-[9px] text-ink/80">15-min bins · GDELT/Telegram backfilled {backfill.hours} h from the sources ({backfill.status}, {backfill.windows} windows) · aircraft and correlations from this server&apos;s own history</span>}
    </div>
    <canvas ref={canvas} className={`min-h-0 w-full flex-1 ${onSeek ? "cursor-pointer" : ""}`} aria-label="Multi-source activity timeline"
      onMouseMove={(event) => { const t = timeAt(event.clientX); if (t === null) return; const rect = event.currentTarget.getBoundingClientRect(); setHover({ x: event.clientX - rect.left, y: event.clientY - rect.top, bin: nearest(t) }); }}
      onMouseLeave={() => setHover(null)}
      onClick={(event) => { const t = timeAt(event.clientX); if (t !== null && onSeek) onSeek(Math.round(t / 60) * 60); }} />
    {hover && <div className="pointer-events-none absolute z-10 max-w-md border border-line bg-panel/95 px-2 py-1 font-mono text-[10px] text-ink" style={{ left: Math.min(hover.x + 24, Math.max(0, (canvas.current?.clientWidth ?? 0) - 260)), top: hover.y + 4 }}>
      <b>{stampUtc(hover.bin.t, true)}</b>{series.map((s) => <span key={s.key} className="ml-2" style={{ color: s.color }}>{valueOf(hover.bin, s.key) ?? "–"} {s.label}</span>)}
    </div>}
  </div>;
}
