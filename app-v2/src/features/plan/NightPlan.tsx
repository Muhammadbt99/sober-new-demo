import { Moon } from "lucide-react";

import type { Point } from "@/lib/api";
import { activeGroups, type CheckIn } from "@/lib/checkin";
import type { NightStep } from "@/lib/contract";
import { useNightPlan } from "@/lib/queries";
import { isNightHour } from "@/lib/risk";
import { cn } from "@/lib/utils";

import "./night-plan.css";

/** Someone who should not drive gets the steps that do not involve driving. */
function forDriver(steps: NightStep[], impaired: boolean): NightStep[] {
  if (!impaired) return steps;
  return [
    ...steps.filter((step) => step.kind === "pickup"),
    ...steps.filter((step) => step.kind === "now" || step.kind === "danger"),
  ];
}

/**
 * "Get home safe": after dark, the trip as a plan in order. The server builds
 * it from what is already scored; the check-in reorders it here, in the browser,
 * so the answers stay on this device.
 */
export function NightPlan({
  origin,
  destination,
  hour,
  day,
  routeIndex,
  checkIn,
  onTryTonight,
  onUseRoute,
}: {
  origin: Point;
  destination: Point;
  hour: number;
  day: number;
  routeIndex: number;
  checkIn: CheckIn;
  /** By day: move the trip to tonight, so the plan can be seen before it is needed. */
  onTryTonight: () => void;
  /** Select a road on the map and the route list. */
  onUseRoute: (index: number) => void;
}) {
  const night = isNightHour(hour);
  const plan = useNightPlan(origin, destination, { hour, day }, night);
  if (!night) {
    return (
      <section className="night-plan is-day" aria-label="Get home safe">
        <div className="night-plan-head">
          <Moon size={14} aria-hidden="true" />
          <strong>Get home safe</strong>
          <span className="mono">from 20:00</span>
        </div>
        <p className="night-plan-note">
          A step-by-step plan for this trip after dark: the danger, when to go, which road, and
          where to be picked up.
        </p>
        <button type="button" className="night-plan-try" onClick={onTryTonight}>
          See tonight&apos;s plan for this trip
        </button>
      </section>
    );
  }
  const impaired = activeGroups(checkIn).includes("impaired");
  const steps = forDriver(plan.data?.steps ?? [], impaired);
  const options = plan.data?.options ?? [];
  const best = plan.data?.best_index;

  return (
    <section className="night-plan" aria-label="Get home safe">
      <div className="night-plan-head">
        <Moon size={14} aria-hidden="true" />
        <strong>Get home safe</strong>
        <span className="mono">night plan</span>
      </div>
      {plan.isLoading && <p className="night-plan-note">Scanning every road for this trip…</p>}
      {plan.isError && (
        <p className="night-plan-note">The night plan could not be built for this trip.</p>
      )}
      {impaired && (
        <p className="night-plan-lead">
          You said you have had alcohol, so this plan does not include driving. Get a taxi or a
          lift from someone sober.
        </p>
      )}
      {steps.length > 0 && (
        <ol className="night-plan-steps">
          {steps.map((step) => (
            <li
              key={`${step.kind}-${step.title}`}
              className={cn(`is-${step.kind}`, step.level && `is-${step.level.toLowerCase()}`)}
            >
              <strong>{step.title}</strong>
              <span>{step.detail}</span>
            </li>
          ))}
        </ol>
      )}
      {!impaired && best !== undefined && best !== routeIndex && (
        <button type="button" className="night-plan-try" onClick={() => onUseRoute(best)}>
          Switch to this road
        </button>
      )}
      {!impaired && options.length > 1 && (
        <details className="night-plan-options">
          <summary>Why not the other {options.length - 1} roads?</summary>
          <ul>
            {options
              .filter((option) => !option.is_best)
              .map((option) => (
                <li key={option.route_index}>
                  <button type="button" onClick={() => onUseRoute(option.route_index)}>
                    <strong>
                      {option.label}: {option.minutes} min, {option.km} km
                    </strong>
                    <span>{option.why}</span>
                  </button>
                </li>
              ))}
          </ul>
        </details>
      )}
    </section>
  );
}
