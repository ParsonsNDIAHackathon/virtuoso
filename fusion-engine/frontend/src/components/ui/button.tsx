import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "command" | "outline" | "critical" | "ghost"; size?: "sm" | "default" };
export function Button({ className, variant = "command", size = "default", ...props }: Props) {
  const variants = { command: "bg-command text-black hover:bg-command/85", outline: "border border-line bg-transparent text-ink hover:bg-white/5", critical: "bg-critical text-white hover:bg-critical/85", ghost: "bg-transparent text-muted hover:bg-white/5 hover:text-ink" };
  return <button className={cn("inline-flex items-center justify-center gap-2 rounded-sm px-3 font-mono text-[11px] font-bold uppercase tracking-wider transition-colors disabled:cursor-not-allowed disabled:opacity-50", size === "sm" ? "h-7" : "h-9", variants[variant], className)} {...props} />;
}
