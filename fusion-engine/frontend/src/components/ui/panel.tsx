import { useEffect, useRef, useState, type HTMLAttributes, type MouseEvent as ReactMouseEvent } from "react";
import { cn } from "../../lib/utils";

/** A panel. Inside the right column (`.sidebar-resizable`) it gains two controls:
 *  - a collapse arrow (top-right) that folds the panel to its title line;
 *  - a drag handle across the bottom edge that sets the panel's height (double-click resets).
 *  Both are remembered per browser, keyed by the panel's first heading text. */
export function Panel({ className, children, ...props }: HTMLAttributes<HTMLElement>) {
  const ref = useRef<HTMLElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const keyRef = useRef<string>("");

  useEffect(() => {
    const el = ref.current; if (!el) return;
    const title = (el.querySelector("h2, h3, p")?.textContent ?? "").trim().slice(0, 40);
    keyRef.current = title ? `panel.${title}` : "";
    try {
      if (keyRef.current) {
        if (localStorage.getItem(`${keyRef.current}.collapsed`) === "1") setCollapsed(true);
        const h = Number(localStorage.getItem(`${keyRef.current}.height`));
        if (h > 0) { el.style.height = `${h}px`; el.style.maxHeight = `${h}px`; el.style.flex = "none"; }
      }
    } catch { /* per-browser convenience only */ }
  }, []);

  const remember = (k: string, v: string | null) => { try { if (keyRef.current) { v === null ? localStorage.removeItem(`${keyRef.current}.${k}`) : localStorage.setItem(`${keyRef.current}.${k}`, v); } } catch { /* ignore */ } };

  const onGrip = (event: ReactMouseEvent<HTMLDivElement>) => {
    const el = ref.current; if (!el || collapsed) return;
    event.preventDefault();
    const startY = event.clientY; const startH = el.getBoundingClientRect().height;
    const move = (e: MouseEvent) => { const h = Math.max(72, Math.min(window.innerHeight * 0.9, startH + (e.clientY - startY))); el.style.height = `${h}px`; el.style.maxHeight = `${h}px`; el.style.flex = "none"; remember("height", String(Math.round(h))); };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); document.body.style.cursor = ""; };
    document.body.style.cursor = "ns-resize";
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  };
  const reset = () => { const el = ref.current; if (el) { el.style.height = ""; el.style.maxHeight = ""; el.style.flex = ""; } remember("height", null); };
  const toggle = () => { setCollapsed((v) => { remember("collapsed", v ? null : "1"); return !v; }); };

  return <section ref={ref} className={cn("panel border border-line bg-panel/95 shadow-panel", collapsed && "panel-collapsed", className)} {...props}>
    <button type="button" className="panel-toggle" title={collapsed ? "Expand panel" : "Collapse panel to its title"} aria-expanded={!collapsed} onClick={toggle}>{collapsed ? "▸" : "▾"}</button>
    {children}
    {!collapsed && <div className="panel-grip" role="separator" aria-orientation="horizontal" title="Drag to resize this panel · double-click to reset" onMouseDown={onGrip} onDoubleClick={reset}><span>⋯ drag</span></div>}
  </section>;
}
