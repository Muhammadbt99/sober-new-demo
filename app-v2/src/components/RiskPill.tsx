import type { RiskLevel } from "@/lib/contract";
import { RISK_COLORS } from "@/lib/risk";
import { cn } from "@/lib/utils";

export function RiskPill({
  level,
  score,
  className,
}: {
  level: RiskLevel;
  score?: number | null;
  className?: string;
}) {
  return (
    <span
      className={cn("risk-pill", className)}
      style={{
        color: RISK_COLORS[level],
        borderColor: `color-mix(in oklch, ${RISK_COLORS[level]} 42%, transparent)`,
        background: `color-mix(in oklch, ${RISK_COLORS[level]} 9%, transparent)`,
      }}
    >
      <span className="risk-pill-dot" style={{ background: RISK_COLORS[level] }} />
      {level}
      {score !== undefined && score !== null && (
        <span className="risk-pill-score mono">{Math.round(score)}</span>
      )}
    </span>
  );
}
