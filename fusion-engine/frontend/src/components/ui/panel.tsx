import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={cn("border border-line bg-panel/95 shadow-panel", className)} {...props} />;
}
