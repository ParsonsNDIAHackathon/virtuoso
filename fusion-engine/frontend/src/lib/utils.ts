export function cn(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export const distanceKm = (a: number, b: number, c: number, d: number) => {
  const radians = Math.PI / 180;
  const value = Math.sin((c - a) * radians / 2) ** 2
    + Math.cos(a * radians) * Math.cos(c * radians) * Math.sin((d - b) * radians / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(value));
};

export const formatWindow = (window?: string | null) => window
  ? `${window.slice(0, 4)}-${window.slice(4, 6)}-${window.slice(6, 8)} ${window.slice(8, 10)}:${window.slice(10, 12)}Z`
  : "–";

export const formatTime = (value?: string | null) => value
  ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
  : "WARMING UP";
