// Accuracy rules: exchange price strings are shown exactly as delivered (no float re-rounding);
// unknown values render "—", never 0; direction always carries a sign, never colour alone.

export type Num = number | string | null | undefined;

export const isNum = (x: Num): x is number | string =>
  x !== null && x !== undefined && x !== "" && Number.isFinite(Number(x));

export const fx = (x: Num, nd = 2): string =>
  isNum(x) ? Number(x).toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd }) : "—";

export const sg = (x: Num, nd = 2, suf = ""): string => {
  if (!isNum(x)) return "—";
  const n = Number(x);
  return (n > 0 ? "+" : n < 0 ? "−" : "") + fx(Math.abs(n), nd) + suf;
};

export const ud = (x: Num): string => (isNum(x) ? (Number(x) > 0 ? "up" : Number(x) < 0 ? "dn" : "") : "");

export const usd = (x: Num): string => {
  if (!isNum(x)) return "—";
  const n = Number(x), a = Math.abs(n);
  const s = a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(2) + "M" : a >= 1e3 ? (a / 1e3).toFixed(1) + "K" : a.toFixed(0);
  return (n < 0 ? "−" : "") + s;
};

/** Exchange price string -> thousands separators, original decimals preserved. */
export const px = (s: Num): string => {
  if (!isNum(s)) return "—";
  const [i, f] = String(s).split(".");
  return Number(i).toLocaleString("en-US") + (f !== undefined ? "." + f : "");
};

export const decimalsOf = (s: Num): number => (String(s ?? "").split(".")[1] || "").length;

const D = (ts: number) => new Date(ts * 1000);
export const hhmm = (ts: number) => D(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
export const hhmmss = (ts: number) => D(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const dt = (ts: number) => D(ts).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
export const age = (s: Num): string => {
  if (!isNum(s)) return "—";
  const n = Number(s);
  return n < 1 ? "<1s" : n < 60 ? `${n | 0}s` : n < 3600 ? `${(n / 60) | 0}m` : `${(n / 3600).toFixed(1)}h`;
};
export const hrs = (m: number) => (m >= 60 ? `${m / 60}h` : `${m}m`);
export const short = (sym: string) => sym.replace("USDT", "");
export const TFL: Record<string, string> = { "1": "1M", "5": "5M", "15": "15M", "60": "1H", "240": "4H" };
export const SERIES_C = ["--s1", "--s2", "--s3", "--s4", "--s5"];

export const cssVar = (v: string): string =>
  typeof window === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(v).trim();
