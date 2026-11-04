import { useMemo } from "react";

import { Notice } from "@/components/DataState";
import type { DepartureSlot, DeparturesResponse } from "@/lib/contract";
import { useDepartures } from "@/lib/queries";
import type { Point } from "@/lib/api";
import { DAY_SHORT } from "@/lib/risk";
import { cn } from "@/lib/utils";

import "./departure-strip.css";

const SLOT_COUNT = 7;

function clock(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

function change(slot: DepartureSlot): string {
  const value = Math.round(slot.change_percent);
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value)}%`;
}

function slotLabel(slot: DepartureSlot, unit: string): string {
  const role = slot.is_chosen
    ? ", your time"
    : slot.is_recommended
      ? ", suggested"
      : slot.is_past
        ? ", already passed"
        : slot.small_hours
          ? ", small hours, not advised"
          : "";
  const city =
    slot.city_change_percent === undefined
      ? ""
      : `, city ${slot.city_change_percent > 0 ? "+" : ""}${Math.round(slot.city_change_percent)}%`;
  const versus = slot.is_chosen ? "" : ` (${change(slot)}${city})`;
  return `${DAY_SHORT[slot.day_of_week]} ${clock(slot.hour)}: ${slot.expected_crashes_total.toFixed(3)} ${unit}${versus}${role}`;
}

function DepartureChart({ data, unit }: { data: DeparturesResponse; unit: string }) {
  const top = useMemo(
    () => Math.max(...data.slots.map((slot) => slot.expected_crashes_total), 1e-9),
    [data.slots],
  );
  return (
    <ol className="departure-chart" aria-label={`${unit} at each departure hour`}>
      {data.slots.map((slot) => {
        const label = slotLabel(slot, unit);
        return (
          <li
            key={slot.offset_hours}
            className={cn(
              "departure-slot",
              slot.is_chosen && "is-chosen",
              slot.is_recommended && "is-recommended",
              slot.small_hours && "is-small-hours",
            )}
            title={label}
          >
            <span className="sr-only">{label}</span>
            <span className="departure-tag" aria-hidden="true">
              {slot.is_recommended ? change(slot) : ""}
            </span>
            <span className="departure-track" aria-hidden="true">
              <span
                className="departure-bar"
                style={{ height: `${Math.max(3, (slot.expected_crashes_total / top) * 100)}%` }}
              />
            </span>
            <span className="departure-hour mono" aria-hidden="true">
              {String(slot.hour).padStart(2, "0")}
            </span>
            <span className="departure-role" aria-hidden="true">
              {slot.is_chosen ? "You" : slot.is_recommended ? "Best" : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The chosen route at the hours around the planned departure. */
export function DepartureStrip({
  origin,
  destination,
  hour,
  day,
  routeIndex,
  from,
  onDeparture,
  onBack,
}: {
  origin: Point;
  destination: Point;
  hour: number;
  day: number;
  routeIndex: number;
  /** The original plan, once a suggested time has been taken. */
  from: { hour: number; day: number } | null;
  onDeparture: (hour: number, day: number) => void;
  onBack: () => void;
}) {
  const query = useDepartures(origin, destination, { hour, day }, routeIndex, from, true);
  const data = query.data;
  const relative = data?.basis === "relative_risk_estimate";
  const measure = relative ? "relative risk" : "expected injury crashes";

  return (
    <section className="departure-strip" aria-label="When to leave">
      <div className="section-heading">
        <span>When to leave</span>
        <span className="mono">this route, 3 h either way</span>
      </div>

      {query.isLoading && (
        <div className="departure-chart departure-chart-loading" aria-label="Scoring departure hours">
          {Array.from({ length: SLOT_COUNT }).map((_, index) => (
            <span key={index} />
          ))}
        </div>
      )}

      {query.isError && (
        <Notice tone="warn">Departure hours could not be scored for this trip.</Notice>
      )}

      {data && !data.available && <Notice tone="info">{data.reason}</Notice>}

      {data && data.available && (
        <>
          <DepartureChart data={data} unit={measure} />
          <p className="departure-text">{data.text}</p>
          {data.verdict === "move" && data.recommended && (
            <button
              type="button"
              className="departure-use"
              onClick={() => onDeparture(data.recommended!.hour, data.recommended!.day_of_week)}
            >
              Use {clock(data.recommended.hour)}
            </button>
          )}
          {from && (
            <button type="button" className="departure-use" onClick={onBack}>
              Back to {clock(from.hour)}
            </button>
          )}
          <p className="departure-note">
            A new time is suggested only if it cuts {measure} by at least {data.threshold_percent}%,
            and by that much more than the city&apos;s own change.
            {data.slots.some((slot) => slot.small_hours) &&
              " Striped bars are the small hours, which are never suggested."}{" "}
            {data.note}
          </p>
        </>
      )}
    </section>
  );
}
