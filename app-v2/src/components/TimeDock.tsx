import { useEffect, useState } from "react";
import { Clock3, Sun } from "lucide-react";

import { DAY_SHORT, formatDateFor, formatHour, formatHour12, upcomingDays } from "@/lib/risk";
import { cn } from "@/lib/utils";

type TimeDockProps = {
  hour: number;
  day: number;
  onChange: (patch: { hour?: number; day?: number }) => void;
  /** What the sun and the day/night map follow: the real clock, or this dock. */
  sunMode?: "live" | "dock";
  onSunMode?: (mode: "live" | "dock") => void;
  disabled?: boolean;
  className?: string;
};

const PRESETS = [
  { label: "After work", hour: 18 },
  { label: "Nightlife", hour: 23 },
  { label: "Late", hour: 3 },
];

export function TimeDock({
  hour,
  day,
  onChange,
  sunMode = "live",
  onSunMode,
  disabled,
  className,
}: TimeDockProps) {
  const [draftHour, setDraftHour] = useState(hour);
  const now = new Date();
  const nowHour = now.getHours();
  const nowDay = (now.getDay() + 6) % 7;

  useEffect(() => {
    setDraftHour(hour);
  }, [hour]);

  function commitHour() {
    if (draftHour !== hour) onChange({ hour: draftHour });
  }

  return (
    <section
      className={cn("time-dock panel", className)}
      aria-label="Time and day controls"
    >
      <div className="time-dock-inner">
        <div className="time-readout">
          <span className="eyebrow">When</span>
          <div className="time-readout-value">
            <strong className="mono">{formatHour(draftHour)}</strong>
            <span>
              {/* "Today" or "Tomorrow" is enough here: the day buttons show the date. */}
              {formatDateFor(day, draftHour, now).split(",")[0]} · {formatHour12(draftHour)}
            </span>
          </div>
        </div>

        <div className="time-slider">
          <div className="time-slider-top">
            <label htmlFor="hour-scrubber" className="sr-only">
              Departure hour
            </label>
            <input
              id="hour-scrubber"
              className="range"
              type="range"
              min={0}
              max={23}
              step={1}
              value={draftHour}
              aria-valuetext={`${formatHour(draftHour)}, ${formatHour12(draftHour)}`}
              disabled={disabled}
              onChange={(event) => setDraftHour(Number(event.target.value))}
              onPointerUp={commitHour}
              onPointerCancel={commitHour}
              onKeyUp={(event) => {
                if (event.key.startsWith("Arrow") || event.key === "Home" || event.key === "End") {
                  commitHour();
                }
              }}
              onBlur={commitHour}
            />
            <div className="tick-row" aria-hidden="true">
              <span>00</span>
              <span>06</span>
              <span>12</span>
              <span>18</span>
              <span>23</span>
            </div>
          </div>
          <div className="time-presets">
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                className={cn("time-preset", hour === preset.hour && "is-active")}
                onClick={() => onChange({ hour: preset.hour })}
                disabled={disabled}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        <div className="time-side">
          <div className="day-row" role="group" aria-label="Day, starting today">
            {upcomingDays(now).map((item) => {
              const label = formatDateFor(item.day, draftHour, now);
              return (
                <button
                  key={item.day}
                  type="button"
                  className={cn(
                    "day-button",
                    day === item.day && "is-active",
                    item.isToday && "is-today",
                  )}
                  aria-pressed={day === item.day}
                  aria-label={label}
                  onClick={() => onChange({ day: item.day })}
                  disabled={disabled}
                  title={label}
                >
                  <span className="day-letter">{DAY_SHORT[item.day]?.slice(0, 2)}</span>
                  <span className="day-date">{item.date}</span>
                </button>
              );
            })}
          </div>
          <div className="now-row">
            <button
              type="button"
              className="now-button"
              onClick={() => onChange({ hour: nowHour, day: nowDay })}
              disabled={disabled}
            >
              <Clock3 size={11} />
              Now
            </button>
            {onSunMode && (
              <button
                type="button"
                className="now-button sun-button"
                aria-pressed={sunMode === "dock"}
                onClick={() => onSunMode(sunMode === "dock" ? "live" : "dock")}
                disabled={disabled}
                title={
                  sunMode === "dock"
                    ? "The sun and the day/night map follow the time on this dock. Press to follow the real clock."
                    : "The sun and the day/night map follow the real clock. Press to make them follow the time on this dock."
                }
              >
                <Sun size={11} aria-hidden="true" />
                Sun · {sunMode === "dock" ? "dock" : "live"}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
