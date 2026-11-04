import { ChevronRight, Layers } from "lucide-react";

import type { Coverage, DangerGroup, DangerProfile } from "@/lib/contract";
import {
  DANGER_LAYERS,
  dangerMeta,
  formatRatio,
  formatShare,
  mixCall,
  type DangerLayer,
  type MixCall,
} from "@/lib/danger";
import { cn } from "@/lib/utils";

const WHERE: Record<string, (area: DangerProfile["area"]) => string> = {
  neighbourhood: (area) => area.neighbourhood ?? "this neighbourhood",
  borough: (area) => area.borough ?? "this borough",
  citywide: () => "all of New York",
};

const CALL_SHORT: Record<MixCall, string> = {
  higher: "more common here",
  usual: "usual mix",
  lower: "less common here",
};

/** Kinds with an estimate first, the most expected first. */
function order(groups: DangerGroup[]) {
  return [...groups].sort(
    (a, b) =>
      Number(a.status !== "ok") - Number(b.status !== "ok") ||
      (b.rate_ratio ?? 0) - (a.rate_ratio ?? 0),
  );
}

/**
 * Where the shares come from, said once: the level most kinds use, then any
 * kind served from a wider area ("drowsy from 61,204 in Manhattan").
 */
function evidenceLine(
  groups: DangerGroup[],
  area: DangerProfile["area"],
  band: string | undefined,
): string | null {
  const levels = new Map<string, { crashes: number; kinds: string[] }>();
  for (const group of groups) {
    if (group.status !== "ok" || !group.evidence) continue;
    const entry = levels.get(group.evidence) ?? { crashes: group.evidence_crashes ?? 0, kinds: [] };
    entry.kinds.push(dangerMeta(group.id).short.toLowerCase());
    levels.set(group.evidence, entry);
  }
  const entries = [...levels.entries()].sort((a, b) => b[1].kinds.length - a[1].kinds.length);
  if (entries.length === 0) return null;
  return entries
    .map(([level, entry], index) => {
      const where = WHERE[level]?.(area) ?? "New York";
      const count = entry.crashes.toLocaleString();
      return index === 0
        ? `From ${count} injury crashes${band ? ` on ${band}s` : ""} in ${where}`
        : `${entry.kinds.join(", ")} from ${count} in ${where}`;
    })
    .join("; ");
}

function GroupRow({
  group,
  band,
  flagged,
}: {
  group: DangerGroup;
  band: string | undefined;
  flagged: boolean;
}) {
  const meta = dangerMeta(group.id);
  const Icon = meta.icon;
  const icon = (
    <span className="danger-icon" style={{ color: meta.color }} aria-hidden="true">
      <Icon size={13} />
    </span>
  );

  if (group.status !== "ok") {
    return (
      <div className="danger-row is-thin">
        <div className="danger-line">
          {icon}
          <span className="danger-name" title={group.label}>
            {meta.short}
            {flagged && <span className="danger-flag">your check-in</span>}
          </span>
          <span className="danger-dim">{group.reason ?? "not enough data"}</span>
        </div>
      </div>
    );
  }

  const call = mixCall(group);
  return (
    <details className="danger-row" data-call={call ?? undefined}>
      <summary className="danger-line">
        {icon}
        <span className="danger-name" title={group.label}>
            {meta.short}
            {flagged && <span className="danger-flag">your check-in</span>}
          </span>
        {call && <span className={cn("danger-call", `is-${call}`)}>{CALL_SHORT[call]}</span>}
        <span className="danger-ratio mono">{formatRatio(group.rate_ratio)}</span>
        <ChevronRight size={12} className="danger-chevron" aria-hidden="true" />
      </summary>
      <p className="danger-detail">
        {group.label}: about {formatRatio(group.rate_ratio)} the crashes of this kind in a
        typical New York hour. Recorded in{" "}
        <span className="mono">{formatShare(group.share)}</span> of injury crashes here (likely{" "}
        {formatShare(group.share_low)}–{formatShare(group.share_high)}), against{" "}
        {formatShare(group.city_band_share ?? group.city_share)} across New York
        {group.city_band_share !== undefined && band ? ` on ${band}s` : ""}.
      </p>
    </details>
  );
}

/**
 * "What kind of crash": the danger types for the selected point, one line
 * each. The number is the expected rate of that kind against a typical New
 * York hour; the share behind it opens on tap. Explains the score; it never
 * changes it.
 */
export function DangerProfileCard({
  profile,
  coverage,
  layer,
  onLayer,
  validated,
  flagged = [],
}: {
  profile: DangerProfile | null | undefined;
  coverage: Coverage | undefined;
  layer: DangerLayer | null;
  onLayer: (layer: DangerLayer | null) => void;
  /** Groups that passed validation (from /health); null while unknown. */
  validated: string[] | null;
  /** Kinds of crash the driver's check-in points to. */
  flagged?: string[];
}) {
  // Danger types come from New York records; elsewhere there is nothing to say.
  if (coverage === "context") return null;

  const shown = profile ? order(profile.groups.filter((g) => g.status !== "not_validated")) : [];
  const off = profile ? profile.groups.filter((g) => g.status === "not_validated") : [];
  const band = profile?.band.label.split(" (")[0]?.toLowerCase();
  const evidence = profile ? evidenceLine(shown, profile.area, band) : null;
  const layers = DANGER_LAYERS.filter((id) => validated === null || validated.includes(id));

  return (
    <div className="danger-card">
      <div className="section-heading">
        <span>Kind of crash{band ? ` · ${band}` : ""}</span>
        {profile && <span className="mono">× typical hour</span>}
      </div>

      {!profile && (
        <p className="section-note">
          No New York crash records cover this exact spot (a river, a park or just outside the
          city), so there is no danger profile.
        </p>
      )}

      {profile && (
        <>
          <div className="danger-list">
            {shown.map((group) => (
              <GroupRow
                key={group.id}
                group={group}
                band={band}
                flagged={flagged.includes(group.id)}
              />
            ))}
          </div>
          <p className="section-note danger-note">
            {evidence ? `${evidence}. ` : ""}
            Police-recorded factors rank places and hours, not drivers, and never change the
            score.
            {off.length > 0 &&
              ` ${off.map((g) => g.label).join(" and ")} ${off.length > 1 ? "are" : "is"} left out: ${
                off.length > 1 ? "they" : "it"
              } failed validation.`}
          </p>
        </>
      )}

      <div className="danger-layers" role="group" aria-label="Map layer">
        <span className="eyebrow">
          <Layers size={10} aria-hidden="true" /> Map
        </span>
        <div className="danger-layer-buttons">
          <button
            type="button"
            className="chip"
            data-active={layer === null || undefined}
            onClick={() => onLayer(null)}
          >
            Overall risk
          </button>
          {layers.map((id) => (
            <button
              key={id}
              type="button"
              className="chip"
              data-active={layer === id || undefined}
              title={`Colour the map by ${dangerMeta(id).label.toLowerCase()}`}
              onClick={() => onLayer(layer === id ? null : id)}
            >
              {dangerMeta(id).short}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
