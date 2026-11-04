import { Gauge, Moon, Signpost, Smartphone, Wine, type LucideIcon } from "lucide-react";

import type { DangerGroup } from "@/lib/contract";

/** How each danger group is named, drawn and coloured. */
export const DANGER_META: Record<string, { label: string; short: string; icon: LucideIcon; color: string }> = {
  impaired: { label: "Impaired driving", short: "Impaired", icon: Wine, color: "#c08cff" },
  drowsy: { label: "Drowsy driving", short: "Drowsy", icon: Moon, color: "#7fb6ff" },
  distracted: { label: "Distracted driving", short: "Distracted", icon: Smartphone, color: "#9aa7b8" },
  speeding: { label: "Speeding", short: "Speeding", icon: Gauge, color: "#ff8a4c" },
  failure_to_yield: { label: "Failure to yield", short: "Not yielding", icon: Signpost, color: "#f3c15d" },
};

export const DANGER_FALLBACK = DANGER_META.distracted!;

export function dangerMeta(id: string) {
  return DANGER_META[id] ?? DANGER_FALLBACK;
}

/**
 * Is this kind of crash a bigger or smaller part of the local mix than it is
 * across the city at the same time of the week? Comparing within the band
 * isolates the place: impaired crashes are a bigger share everywhere on
 * weekend nights, and the expected-rate number already carries that. Decided
 * on the 80% interval, not the point estimate: a share whose interval
 * straddles the city's is "about usual", however far its centre sits from it.
 */
export type MixCall = "higher" | "usual" | "lower";

export function mixCall(group: DangerGroup): MixCall | null {
  if (group.status !== "ok" || group.share === undefined) return null;
  const city = group.city_band_share ?? group.city_share;
  if (city === undefined || !(city > 0)) return null;
  const ratio = group.share / city;
  const { share_low: low, share_high: high } = group;
  if (low === undefined || high === undefined) {
    return ratio >= 1.25 ? "higher" : ratio <= 0.8 ? "lower" : "usual";
  }
  if (low > city && ratio >= 1.25) return "higher";
  if (high < city && ratio <= 0.8) return "lower";
  return "usual";
}

export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value >= 9.95) return `${Math.round(value)}×`;
  return `${value.toFixed(1)}×`;
}

export function formatShare(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const percent = value * 100;
  return percent < 1 ? `${percent.toFixed(2)}%` : `${percent.toFixed(1)}%`;
}

/**
 * Map colours for a danger layer, by the cell's expected rate of that kind of
 * crash against a typical NYC cell-hour. Six steps: on a Saturday night the
 * impaired-driving layer runs from 0.15x (10th percentile of cells) to 6x (90th)
 * with a few cells near 15x, so the top step separates the hottest places.
 */
export const LAYER_STEPS: Array<{ upTo: number | null; color: string; label: string }> = [
  { upTo: 0.5, color: "#2f6f8f", label: "< 0.5×" },
  { upTo: 1, color: "#4f9fb8", label: "0.5–1×" },
  { upTo: 2, color: "#e7c160", label: "1–2×" },
  { upTo: 4, color: "#f08a4b", label: "2–4×" },
  { upTo: 8, color: "#e8475f", label: "4–8×" },
  { upTo: null, color: "#b0177a", label: "8× +" },
];

/** Every group that can be a map layer; ones that failed validation are shown disabled. */
export const DANGER_LAYERS = ["impaired", "drowsy", "speeding", "failure_to_yield", "distracted"] as const;
export type DangerLayer = (typeof DANGER_LAYERS)[number];

export function isDangerLayer(value: string | null): value is DangerLayer {
  return value !== null && (DANGER_LAYERS as readonly string[]).includes(value);
}
