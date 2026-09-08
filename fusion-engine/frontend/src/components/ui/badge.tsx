import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("inline-flex items-center gap-1 border border-line bg-black/20 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-muted", className)} {...props} />;
}
