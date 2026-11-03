import type { RiskTrendResponse } from "@/lib/contract";
import { RISK_COLORS, formatHour } from "@/lib/risk";
import { cn } from "@/lib/utils";

export function TrendStrip({
  data,
  hour,
  onHour,
}: {
  data: RiskTrendResponse | undefined;
  hour: number;
  onHour: (hour: number) => void;
}) {
  if (!data) {
    return (
      <div className="trend-strip trend-strip-empty" aria-label="Hourly risk trend loading">
        {Array.from({ length: 24 }).map((_, index) => (
          <span key={index} style={{ height: `${28 + ((index * 17) % 42)}%` }} />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="trend-head">
        <span className="eyebrow">Risk by hour</span>
        <span className="trend-peak mono">
          peak {formatHour(data.peak_risk.hour)} · safest {formatHour(data.safest_time.hour)}
        </span>
      </div>
      <div className="trend-strip" role="group" aria-label="Hourly risk trend">
        {data.trend.map((entry) => {
          const height = Math.max(7, entry.risk_score);
          return (
            <button
              key={entry.hour}
              type="button"
              className={cn("trend-bar", entry.hour === hour && "is-active")}
              style={{
                height: `${height}%`,
                background: RISK_COLORS[entry.risk_level],
                opacity: entry.hour === hour ? 1 : 0.42,
              }}
              onClick={() => onHour(entry.hour)}
              title={`${entry.hour_label} · ${entry.risk_level} · ${Math.round(entry.risk_score)}`}
              aria-label={`${entry.hour_label}, ${entry.risk_level}, score ${Math.round(entry.risk_score)}`}
              aria-pressed={entry.hour === hour}
            />
          );
        })}
      </div>
      <div className="trend-axis mono" aria-hidden="true">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>23</span>
      </div>
    </div>
  );
}
