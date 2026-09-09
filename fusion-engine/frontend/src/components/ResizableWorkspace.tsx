import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

const DEFAULT_WIDTH = 390;
const MIN_WIDTH = 320;
const MIN_MAP_WIDTH = 400;
const STORAGE_KEY = "fusion.sidebar-width";

function savedWidth() {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(value) && value >= MIN_WIDTH ? value : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

export function ResizableWorkspace({ map, sidebar, timeline }: { map: ReactNode; sidebar: ReactNode; timeline: ReactNode }) {
  const container = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);
  const [preferredWidth, setPreferredWidth] = useState(savedWidth);
  const [maxWidth, setMaxWidth] = useState(() => Math.max(MIN_WIDTH, window.innerWidth - MIN_MAP_WIDTH - 40));
  const [dragging, setDragging] = useState(false);
  const width = Math.min(maxWidth, Math.max(MIN_WIDTH, preferredWidth));
  const widthRef = useRef(width);
  widthRef.current = width;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => setMaxWidth(Math.max(MIN_WIDTH, Math.floor(element.clientWidth - MIN_MAP_WIDTH - 40)));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const { cursor, userSelect } = document.body.style;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.cursor = cursor;
      document.body.style.userSelect = userSelect;
    };
  }, [dragging]);

  const resize = (value: number, persist = false) => {
    const next = Math.round(Math.min(maxWidth, Math.max(MIN_WIDTH, value)));
    widthRef.current = next;
    setPreferredWidth(next);
    if (persist) {
      try { window.localStorage.setItem(STORAGE_KEY, String(next)); } catch { /* Resizing still works without storage. */ }
    }
  };
  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    resize(widthRef.current, true);
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 64 : 16;
    const next = event.key === "ArrowLeft" ? width + step : event.key === "ArrowRight" ? width - step
      : event.key === "Home" ? MIN_WIDTH : event.key === "End" ? maxWidth : event.key === "Enter" ? DEFAULT_WIDTH : null;
    if (next === null) return;
    event.preventDefault();
    resize(next, true);
  };

  return <div ref={container} className="grid min-h-0 gap-2 p-2 lg:grid-cols-[minmax(0,1fr)_8px_var(--sidebar-width)] lg:grid-rows-[minmax(0,1fr)_240px]" style={{ "--sidebar-width": `${width}px` } as CSSProperties}>
    {map}
    <div role="separator" aria-orientation="vertical" aria-label="Resize right panes" aria-controls="right-panes"
      aria-valuemin={MIN_WIDTH} aria-valuemax={maxWidth} aria-valuenow={width} aria-valuetext={`${width} pixels wide`} tabIndex={0}
      title="Drag to resize the right panes. Use Left/Right arrows when focused; double-click or press Enter to reset."
      className={`group relative hidden cursor-col-resize touch-none select-none items-center justify-center rounded-sm outline-none hover:bg-white/5 focus-visible:ring-1 focus-visible:ring-command lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:flex ${dragging ? "bg-command/10" : ""}`}
      onPointerDown={(event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        event.preventDefault();
        event.currentTarget.focus();
        drag.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: width };
        event.currentTarget.setPointerCapture(event.pointerId);
        setDragging(true);
      }}
      onPointerMove={(event) => {
        const current = drag.current;
        if (current?.pointerId === event.pointerId) resize(current.startWidth + current.startX - event.clientX);
      }}
      onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag}
      onDoubleClick={() => resize(DEFAULT_WIDTH, true)} onKeyDown={onKeyDown}>
      <span aria-hidden className={`h-12 w-1 rounded-full transition-colors group-hover:bg-command group-focus-visible:bg-command ${dragging ? "bg-command" : "bg-line"}`} />
    </div>
    {sidebar}
    {timeline}
  </div>;
}
