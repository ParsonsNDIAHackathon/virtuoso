import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "hsl(var(--canvas))",
        panel: "hsl(var(--panel))",
        line: "hsl(var(--line))",
        ink: "hsl(var(--ink))",
        muted: "hsl(var(--muted))",
        command: "hsl(var(--command))",
        critical: "hsl(var(--critical))",
      },
      fontFamily: { sans: ["Geist", "Arial", "sans-serif"], mono: ["Geist Mono", "ui-monospace", "monospace"] },
      boxShadow: { panel: "0 18px 45px rgb(0 0 0 / 0.22)" },
    },
  },
  plugins: [],
} satisfies Config;
