import { useState } from "react";

import { ErrorBlock, LoadingBlock } from "@/components/DataState";
import type { Point, TimeParams } from "@/lib/api";
import { useSeverity } from "@/lib/queries";

/** Values are phrases the server's text matcher understands (severity_features.py). */
const VEHICLES: Array<[string, string]> = [
  ["", "Any vehicle"],
  ["bicycle", "Bicycle"],
  ["e-bike", "E-bike"],
  ["scooter", "Scooter or moped"],
  ["motorcycle", "Motorcycle"],
  ["truck", "Truck"],
  ["bus", "Bus"],
  ["van", "Van"],
  ["taxi", "Taxi"],
  ["sport utility", "SUV"],
  ["sedan", "Sedan"],
];

const FACTORS: Array<[string, string]> = [
  ["", "No cause given"],
  ["Unsafe Speed", "Unsafe speed"],
  ["Alcohol Involvement", "Alcohol"],
  ["Driver Inattention/Distraction", "Distraction"],
  ["Failure to Yield Right-of-Way", "Failure to yield"],
  ["Following Too Closely", "Following too closely"],
  ["Passing or Lane Usage Improper", "Improper lane use"],
  ["Traffic Control Disregarded", "Traffic control disregarded"],
  ["Backing Unsafely", "Backing unsafely"],
  ["Turning Improperly", "Turning improperly"],
  ["Driver Inexperience", "Driver inexperience"],
  ["View Obstructed/Limited", "View obstructed"],
  ["Aggressive Driving/Road Rage", "Aggressive driving"],
  ["Pavement Slippery", "Slippery pavement"],
  ["Fell Asleep", "Fell asleep or fatigued"],
  ["Brakes Defective", "Defective brakes"],
  ["Pedestrian/Bicyclist/Other Pedestrian Error/Confusion", "Pedestrian error"],
];

const CLASSES: Array<{ id: string; label: string; color: string }> = [
  { id: "property_damage_only", label: "Property damage only", color: "#59e3b0" },
  { id: "injury_likely", label: "Injury likely", color: "#f3c15d" },
  { id: "fatal_risk", label: "Fatal risk", color: "#ff5268" },
];

/**
 * "If a crash happened here…" The severity model describes a crash that has
 * already occurred (it needs the vehicle and the cause), so this is a what-if
 * lab and says so. It never feeds the risk score.
 */
export function SeverityLab({ point, time }: { point: Point; time: TimeParams }) {
  const [vehicle, setVehicle] = useState("");
  const [factor, setFactor] = useState("");
  const severity = useSeverity(point, time, { vehicle, factor });
  const data = severity.data;

  return (
    <div className="severity-lab">
      <div className="severity-flags">
        <span className="chip chip-warn">Research only</span>
        <span className="chip">Not in the risk score</span>
      </div>
      <p className="section-note">
        Pick a vehicle and a cause to see how a crash at this place and hour is likely to turn
        out. It describes crashes that happened, so it cannot tell you where one will.
      </p>

      <div className="severity-controls">
        <label>
          <span className="eyebrow">Vehicle</span>
          <select value={vehicle} onChange={(event) => setVehicle(event.target.value)}>
            {VEHICLES.map(([value, label]) => (
              <option key={value || "any"} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="eyebrow">Cause</span>
          <select value={factor} onChange={(event) => setFactor(event.target.value)}>
            {FACTORS.map(([value, label]) => (
              <option key={value || "none"} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {severity.isLoading && !data && <LoadingBlock lines={3} />}
      {severity.isError && !data && (
        <ErrorBlock
          error={severity.error}
          onRetry={() => void severity.refetch()}
          title="Severity model unavailable"
        />
      )}
      {data && (
        <div className="severity-result" data-stale={severity.isPlaceholderData}>
          <div className="severity-verdict">
            <span className="eyebrow">Most likely outcome</span>
            <strong>
              {CLASSES.find((item) => item.id === data.predicted)?.label ?? data.predicted}
            </strong>
            <span className="mono">{Math.round(data.confidence * 100)}% model confidence</span>
          </div>
          <div className="severity-bars">
            {CLASSES.map((item) => {
              const share = data.probabilities[item.id] ?? 0;
              return (
                <div key={item.id} className="severity-bar">
                  <span className="severity-bar-label">
                    <span>{item.label}</span>
                    <span className="mono">{Math.round(share * 100)}%</span>
                  </span>
                  <div className="risk-bar" aria-hidden="true">
                    <span style={{ width: `${Math.min(100, share * 100)}%`, background: item.color }} />
                  </div>
                </div>
              );
            })}
          </div>
          <p className="section-note severity-note">
            Class-weighted, not calibrated: compare scenarios with each other and do not read
            “fatal risk” as a real fatality rate.
          </p>
        </div>
      )}
    </div>
  );
}
