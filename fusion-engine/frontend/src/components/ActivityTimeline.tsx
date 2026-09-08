import { useEffect, useRef } from "react";
import type { Timeline } from "../lib/types";

const series = [
  ["events", "#eab85a", "OSINT"],
  ["conflict", "#df5e55", "Conflict"],
  ["social", "#d99add", "Telegram"],
  ["military", "#5cc7da", "Military air"],
  ["firms_new", "#94c973", "New thermal"],
] as const;

export function ActivityTimeline({ timeline, activeTime }: { timeline?: Timeline; activeTime?: number | null }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => {
      const rect = element.getBoundingClientRect(); const ratio = window.devicePixelRatio || 1;
      element.width = Math.max(1, Math.floor(rect.width * ratio)); element.height = Math.max(1, Math.floor(rect.height * ratio));
      const context = element.getContext("2d"); if (!context) return;
      context.scale(ratio, ratio); context.clearRect(0, 0, rect.width, rect.height);
      const bins = timeline?.bins ?? []; if (!bins.length) return;
      const padding = { left: 12, right: 12, top: 24, bottom: 18 }; const width = rect.width - padding.left - padding.right; const height = rect.height - padding.top - padding.bottom;
      context.strokeStyle = "rgba(105, 123, 100, .28)"; context.lineWidth = 1;
      for (let row = 0; row < 4; row += 1) { const y = padding.top + row * height / 3; context.beginPath(); context.moveTo(padding.left, y); context.lineTo(rect.width - padding.right, y); context.stroke(); }
      for (const [key, color] of series) {
        const maximum = Math.max(1, ...bins.map((bin) => bin[key] ?? 0)); context.strokeStyle = color; context.lineWidth = 1.5; context.beginPath();
        bins.forEach((bin, index) => { const x = padding.left + index / Math.max(1, bins.length - 1) * width; const y = padding.top + height - ((bin[key] ?? 0) / maximum) * height; if (index === 0) context.moveTo(x, y); else context.lineTo(x, y); }); context.stroke();
      }
      if (activeTime) { const start = bins[0].t; const end = bins[bins.length - 1].t; if (end > start) { const x = padding.left + Math.max(0, Math.min(1, (activeTime - start) / (end - start))) * width; context.strokeStyle = "#e5e7df"; context.beginPath(); context.moveTo(x, padding.top); context.lineTo(x, padding.top + height); context.stroke(); } }
      context.fillStyle = "#8a9885"; context.font = "10px Geist Mono, monospace"; context.fillText(new Date(bins[0].t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" }), padding.left, rect.height - 4); context.textAlign = "right"; context.fillText(new Date(bins[bins.length - 1].t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" }), rect.width - padding.right, rect.height - 4); context.textAlign = "left";
    };
    draw(); const observer = new ResizeObserver(draw); observer.observe(element); return () => observer.disconnect();
  }, [timeline, activeTime]);
  return <div className="flex h-full min-h-[200px] flex-col p-3"><div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">{series.map(([, color, label]) => <span key={label} className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-wide text-muted"><i className="h-0.5 w-3" style={{ background: color }} />{label}</span>)}</div><canvas ref={canvas} className="min-h-0 w-full flex-1" aria-label="Multi-source activity timeline" /></div>;
}
