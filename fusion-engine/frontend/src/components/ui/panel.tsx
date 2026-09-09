import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type HTMLAttributes, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { cn } from "../../lib/utils";

/** Inside a <PanelGroup> (the right column) every Panel has one structure: a 32 px collapse button,
 *  a body that scrolls on its own and is really hidden when collapsed, and an opaque resize footer
 *  outside the scrolling content. Outside a group a Panel is a plain bordered section (the map). */
const Group = createContext(false);
export function PanelGroup({ children }: { children: React.ReactNode }) { return <Group.Provider value={true}>{children}</Group.Provider>; }

type PanelProps = HTMLAttributes<HTMLElement> & { panelId?: string; openOn?: string | number };

export function Panel({ className, children, panelId, openOn, ...props }: PanelProps) {
  const structured = useContext(Group);
  if (!structured) return <section className={cn("panel border border-line bg-panel/95 shadow-panel", className)} {...props}>{children}</section>;
  return <StructuredPanel className={className} panelId={panelId} openOn={openOn} {...props}>{children}</StructuredPanel>;
}

const MIN_H = 60;
const store = { get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k: string, v: string | null) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* per-browser convenience only */ } } };

function StructuredPanel({ className, children, panelId, openOn, ...props }: PanelProps) {
  const ref = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [title, setTitle] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [height, setHeight] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  // Stable identity: an explicit panelId, else the first heading text (read once, on mount).
  useLayoutEffect(() => { const el = bodyRef.current; if (!el) return; setTitle((el.querySelector("h1, h2, h3, p")?.textContent ?? "").trim().slice(0, 48)); }, []);
  const key = panelId ? `panel.${panelId}` : title ? `panel.${title}` : "";
  useEffect(() => {
    if (!key) return;
    if (openOn !== undefined) {
      setCollapsed(false);
      store.set(`${key}.collapsed`, null);
      return;
    }
    setCollapsed(store.get(`${key}.collapsed`) === "1");
    const h = Number(store.get(`${key}.height`)); setHeight(h >= MIN_H ? h : null);
  }, [key, openOn]);

  const toggle = () => { setCollapsed((v) => { store.set(`${key}.collapsed`, v ? null : "1"); return !v; }); };
  const apply = (h: number | null) => { setHeight(h); store.set(`${key}.height`, h === null ? null : String(Math.round(h))); };
  const clamp = (h: number) => Math.max(MIN_H, Math.min(window.innerHeight * 0.9, h));

  const onGripDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = ref.current; if (!el) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
    const startY = e.clientY; const startH = el.getBoundingClientRect().height; setDragging(true);
    const move = (ev: PointerEvent) => apply(clamp(startH + (ev.clientY - startY)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); setDragging(false); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
  };
  const onGripKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const el = ref.current; if (!el) return;
    const cur = el.getBoundingClientRect().height;
    if (e.key === "ArrowUp") { e.preventDefault(); apply(clamp(cur - 24)); }
    else if (e.key === "ArrowDown") { e.preventDefault(); apply(clamp(cur + 24)); }
    else if (e.key === "Home" || e.key === "Escape") { e.preventDefault(); apply(null); }
  };

  const style = !collapsed && height ? { height: `${height}px`, maxHeight: `${height}px`, flex: "none" } : undefined;
  return <section ref={ref} style={style} className={cn("panel panel-structured border border-line bg-panel/95 shadow-panel", collapsed && "panel-collapsed", className)} {...props}>
    <button type="button" className="panel-toggle" title={collapsed ? "Expand panel" : "Collapse panel to its title"} aria-expanded={!collapsed} aria-label={collapsed ? `Expand ${title || "panel"}` : `Collapse ${title || "panel"}`} onClick={toggle}>{collapsed ? "▸" : "▾"}</button>
    {collapsed && <div className="panel-bar" onClick={toggle} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}>{title || "Panel"}</div>}
    <div ref={bodyRef} className="panel-body" hidden={collapsed}>{children}</div>
    {!collapsed && <div className={cn("panel-grip", dragging && "dragging")} role="separator" aria-orientation="horizontal" aria-label={`Resize ${title || "panel"}`} tabIndex={0}
      title="Drag to resize · double-click or Home resets · arrow keys adjust" onPointerDown={onGripDown} onDoubleClick={() => apply(null)} onKeyDown={onGripKey}><span>⋯ drag</span></div>}
  </section>;
}
