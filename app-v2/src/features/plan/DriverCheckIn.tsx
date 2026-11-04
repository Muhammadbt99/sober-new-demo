import { HeartPulse, OctagonAlert, TriangleAlert } from "lucide-react";

import { Disclosure } from "@/components/Disclosure";
import type { Point } from "@/lib/api";
import {
  AWAKE_OPTIONS,
  activeGroups,
  assess,
  checkInSummary,
  restStops,
  type CheckIn,
} from "@/lib/checkin";
import type { Route } from "@/lib/contract";
import { useNearestSafe } from "@/lib/queries";
import { cn } from "@/lib/utils";

import "./checkin.css";

/**
 * "What danger am I bringing?" The answers meet this route's crash records in
 * plain warnings. They live only in this page's memory.
 */
export function DriverCheckIn({
  value,
  onChange,
  route,
  origin,
}: {
  value: CheckIn;
  onChange: (value: CheckIn) => void;
  route: Route | undefined;
  origin: Point | null;
}) {
  const notices = assess(value, route);
  const drowsy = activeGroups(value).includes("drowsy");
  const nearby = useNearestSafe(origin ?? { lat: 0, lon: 0 }, drowsy && origin !== null);
  const rests = restStops(nearby.data?.stops ?? []);

  return (
    <div className="checkin">
      {notices.map((notice) => (
        <div key={notice.title} className={cn("checkin-notice", `is-${notice.tone}`)} role="status">
          {notice.tone === "stop" ? (
            <OctagonAlert size={16} aria-hidden="true" />
          ) : (
            <TriangleAlert size={16} aria-hidden="true" />
          )}
          <div>
            <strong>{notice.title}</strong>
            <p>{notice.detail}</p>
          </div>
        </div>
      ))}
      {drowsy && rests.length > 0 && (
        <p className="checkin-rests">
          Near your start:{" "}
          {rests
            .map((stop) => `${stop.name} (${stop.distance_km.toFixed(1)} km)`)
            .join(", ")}
        </p>
      )}

      <Disclosure
        title="Driver check-in"
        icon={<HeartPulse size={13} aria-hidden="true" />}
        meta={checkInSummary(value)}
      >
        <div className="checkin-form">
          <div role="group" aria-label="Hours awake">
            <span className="checkin-label">How long have you been awake?</span>
            <div className="checkin-options">
              {AWAKE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={cn(value.awake === option.id && "is-active")}
                  aria-pressed={value.awake === option.id}
                  onClick={() =>
                    onChange({ ...value, awake: value.awake === option.id ? null : option.id })
                  }
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <label className="checkin-toggle">
            <input
              type="checkbox"
              checked={value.nightShift}
              onChange={(event) => onChange({ ...value, nightShift: event.target.checked })}
            />
            I have just finished a night shift
          </label>
          <label className="checkin-toggle">
            <input
              type="checkbox"
              checked={value.alcohol}
              onChange={(event) => onChange({ ...value, alcohol: event.target.checked })}
            />
            I have had alcohol
          </label>
          <p className="checkin-note">
            Self-reported, and not a medical or legal finding. It never changes the risk score.
            Your answers stay on this device and are gone when you close the page.
          </p>
        </div>
      </Disclosure>
    </div>
  );
}
