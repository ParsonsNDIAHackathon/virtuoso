import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Frame } from "lucide-react";
import { api } from "../lib/api";

/** Inline preview card for a source link (Open Graph summary fetched once by the engine).
 *  Publishers usually forbid iframes, so the card is the default; "Embed" is offered only when
 *  the provider allows framing (Telegram official embed, adsb.lol) and opens the overlay viewer. */
export function SourcePreview({ href, label, onEmbed }: { href: string; label?: string; onEmbed?: () => void }) {
  const q = useQuery({ queryKey: ["preview", href], queryFn: () => api.preview(href), staleTime: 6 * 3600 * 1000, retry: false });
  const p = q.data;
  const host = (() => { try { return new URL(href).hostname.replace(/^www\./, ""); } catch { return href; } })();
  const canEmbed = onEmbed && (p?.embeddable || /(^|\.)t\.me$/.test(host) || /(^|\.)adsb\.lol$/.test(host));
  return <div className="mt-2 border border-line bg-black/20 p-2 text-xs">
    <div className="flex items-center gap-2">
      <p className="font-mono text-[9px] uppercase tracking-[.14em] text-muted">{label ?? "Source"} · {p?.site_name ?? host}</p>
      <div className="ml-auto flex gap-1">
        {canEmbed && <button className="inline-flex items-center gap-1 border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase text-muted hover:text-ink" onClick={onEmbed} title="Open in the embedded viewer"><Frame size={10} /> Embed</button>}
        <a className="inline-flex items-center gap-1 border border-command/60 px-1.5 py-0.5 font-mono text-[9px] uppercase text-command hover:underline" href={href} target="_blank" rel="noopener noreferrer"><ExternalLink size={10} /> Open</a>
      </div>
    </div>
    {q.isLoading && <p className="mt-1 text-[10px] text-muted">fetching preview…</p>}
    {p && (p.title || p.description) && <div className="mt-1 flex gap-2">
      {p.image && <img src={p.image} alt="" className="h-14 w-20 flex-none object-cover" loading="lazy" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />}
      <div className="min-w-0">
        {p.title && <p className="font-semibold leading-snug text-ink">{p.title}</p>}
        {p.description && <p className="mt-0.5 line-clamp-3 text-[11px] leading-snug text-muted">{p.description}</p>}
        {p.published && <p className="mt-0.5 font-mono text-[9px] text-muted">{p.published}</p>}
      </div>
    </div>}
    {p && !p.title && !p.description && !q.isLoading && <p className="mt-1 text-[10px] text-muted">No preview available{p.error ? ` (${p.error})` : ""} — open the source directly.</p>}
    {p && !p.embeddable && <p className="mt-1 font-mono text-[9px] text-muted">Publisher blocks embedding; this card is the in-console view.</p>}
  </div>;
}
