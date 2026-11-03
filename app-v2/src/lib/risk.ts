import type { RiskLevel, SegmentBucket } from "@/lib/contract";

export const RISK_COLORS: Record<RiskLevel, string> = {
  LOW: "#59e3b0",
  MEDIUM: "#f3c15d",
  HIGH: "#ff8a4c",
  CRITICAL: "#ff5268",
  UNKNOWN: "#748295",
};

export const RISK_SOFT_COLORS: Record<RiskLevel, string> = {
  LOW: "rgba(89, 227, 176, 0.14)",
  MEDIUM: "rgba(243, 193, 93, 0.16)",
  HIGH: "rgba(255, 138, 76, 0.17)",
  CRITICAL: "rgba(255, 82, 104, 0.19)",
  UNKNOWN: "rgba(116, 130, 149, 0.12)",
};

export const BUCKET_COLORS: Record<SegmentBucket, string> = {
  low: "#59e3b0",
  medium: "#f3c15d",
  high: "#ff8a4c",
  extreme: "#ff5268",
  uncovered: "#748295",
};

export const BUCKET_LABELS: Record<SegmentBucket, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  extreme: "Extreme",
  uncovered: "Uncovered",
};

export const DAY_NAMES = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

export const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export function formatHour(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

export function formatHour12(hour: number): string {
  const suffix = hour >= 12 ? "PM" : "AM";
  const value = hour % 12 === 0 ? 12 : hour % 12;
  return `${value} ${suffix}`;
}

export function bandFromScore(score: number): RiskLevel {
  if (score >= 75) return "CRITICAL";
  if (score >= 50) return "HIGH";
  if (score >= 25) return "MEDIUM";
  return "LOW";
}

/** English ordinal suffix for a number as written: 1st, 2nd, 3rd, 11th, 93rd, 95.2nd. */
function ordinalSuffix(text: string): string {
  const lastTwo = Number(text.replace(/\D/g, "").slice(-2));
  if (!text.includes(".") && lastTwo >= 11 && lastTwo <= 13) return "th";
  const last = Number(text.slice(-1));
  return last === 1 ? "st" : last === 2 ? "nd" : last === 3 ? "rd" : "th";
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const text = value.toFixed(digits);
  return `${text}${ordinalSuffix(text)}`;
}

export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  if (Math.abs(value) < 0.0001) return "<0.0001";
  return value.toFixed(4);
}

export function formatCrashes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  if (Math.abs(value) < 0.001) return "<0.001";
  return value.toFixed(3);
}

export function formatDuration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return "—";
  const rounded = Math.round(minutes);
  if (rounded < 60) return `${rounded} min`;
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function formatDistance(km: number | null | undefined): string {
  if (km === null || km === undefined || !Number.isFinite(km)) return "—";
  return `${km.toFixed(1)} km`;
}

export function percentChange(value: number, baseline: number): string {
  if (!baseline) return "—";
  return `${((value / baseline - 1) * 100).toFixed(1)}%`;
}

export function scoreTone(level: RiskLevel): string {
  switch (level) {
    case "LOW":
      return "text-[var(--color-safe)]";
    case "MEDIUM":
      return "text-[var(--color-caution)]";
    case "HIGH":
      return "text-[var(--color-high)]";
    case "CRITICAL":
      return "text-[var(--color-danger)]";
    default:
      return "text-[var(--color-muted)]";
  }
}

export function scoreBorder(level: RiskLevel): string {
  switch (level) {
    case "LOW":
      return "border-[color-mix(in_oklch,var(--color-safe)_45%,transparent)]";
    case "MEDIUM":
      return "border-[color-mix(in_oklch,var(--color-caution)_45%,transparent)]";
    case "HIGH":
      return "border-[color-mix(in_oklch,var(--color-high)_45%,transparent)]";
    case "CRITICAL":
      return "border-[color-mix(in_oklch,var(--color-danger)_45%,transparent)]";
    default:
      return "border-[var(--color-line)]";
  }
}

export const COVERAGE_LABEL = {
  model: "Model coverage",
  mixed: "Partial model coverage",
  rule_based_only: "Outside model coverage",
  context: "Global estimate",
} as const;

export function weatherLabel(source: string | undefined): string {
  if (!source) return "Weather unknown";
  if (source === "live") return "Live weather";
  if (source === "fallback") return "Weather unavailable";
  if (source === "stale") return "Cached weather";
  return `Weather: ${source}`;
}
