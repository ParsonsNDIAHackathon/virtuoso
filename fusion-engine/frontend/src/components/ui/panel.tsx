import { useRef, type HTMLAttributes, type MouseEvent as ReactMouseEvent } from "react";
import { cn } from "../../lib/utils";

/** A panel. Inside the right column (`.sidebar-resizable`) it shows a drag bar at its bottom edge;
 *  dragging sets an explicit height (and lifts the panel's default height cap) so any panel can be
 *  made taller or shorter, and it scrolls inside itself. */
export function Panel({ className, children, ...props }: HTMLAttributes<HTMLElement>) {
  const ref = useRef<HTMLElement>(null);
  const onGrip = (event: ReactMouseEvent<HTMLDivElement>) => {
    const el = ref.current; if (!el) return;
    event.preventDefault();
    const startY = event.clientY; const startH = el.getBoundingClientRect().height;
    const move = (e: MouseEvent) => { const h = Math.max(72, Math.min(window.innerHeight * 0.9, startH + (e.clientY - startY))); el.style.height = `${h}px`; el.style.maxHeight = `${h}px`; el.style.flex = "none"; };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); document.body.style.cursor = ""; };
    document.body.style.cursor = "ns-resize";
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  };
  return <section ref={ref} className={cn("panel border border-line bg-panel/95 shadow-panel", className)} {...props}>
    {children}
    <div className="panel-grip" role="separator" aria-orientation="horizontal" title="Drag to resize this panel" onMouseDown={onGrip}
      onDoubleClick={() => { const el = ref.current; if (el) { el.style.height = ""; el.style.maxHeight = ""; el.style.flex = ""; } }}>
      <span /></div>
  </section>;
}
