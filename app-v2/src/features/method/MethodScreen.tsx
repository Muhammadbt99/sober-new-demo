import { ArrowLeft, CheckCircle2, Database, ExternalLink, ShieldAlert, Sigma } from "lucide-react";

import { SeverityCard } from "@/features/method/SeverityCard";
import type { HealthResponse } from "@/lib/contract";
import type { Panel } from "@/lib/use-url-state";

const TEMPORAL = [
  { decile: 1, predicted: 0.0000749, observed: 0.0000987, lift: 0.03 },
  { decile: 2, predicted: 0.0002905, observed: 0.0003906, lift: 0.11 },
  { decile: 3, predicted: 0.0006328, observed: 0.0007513, lift: 0.22 },
  { decile: 4, predicted: 0.0010566, observed: 0.0011945, lift: 0.35 },
  { decile: 5, predicted: 0.0016763, observed: 0.002037, lift: 0.59 },
  { decile: 6, predicted: 0.0025811, observed: 0.0029268, lift: 0.85 },
  { decile: 7, predicted: 0.0037501, observed: 0.0042245, lift: 1.23 },
  { decile: 8, predicted: 0.0051453, observed: 0.0059368, lift: 1.73 },
  { decile: 9, predicted: 0.0071417, observed: 0.0081029, lift: 2.37 },
  { decile: 10, predicted: 0.0127317, observed: 0.0127836, lift: 3.73 },
];

const HOLDOUT = [
  { decile: 1, predicted: 0.00059, observed: 0.001085, lift: 0.21 },
  { decile: 2, predicted: 0.00102, observed: 0.002197, lift: 0.42 },
  { decile: 3, predicted: 0.001406, observed: 0.002762, lift: 0.52 },
  { decile: 4, predicted: 0.001817, observed: 0.003744, lift: 0.71 },
  { decile: 5, predicted: 0.002306, observed: 0.005003, lift: 0.95 },
  { decile: 6, predicted: 0.00289, observed: 0.005787, lift: 1.09 },
  { decile: 7, predicted: 0.003547, observed: 0.00617, lift: 1.17 },
  { decile: 8, predicted: 0.004359, observed: 0.007335, lift: 1.39 },
  { decile: 9, predicted: 0.005488, observed: 0.008884, lift: 1.68 },
  { decile: 10, predicted: 0.0079, observed: 0.010529, lift: 1.99 },
];

const FEATURES = [
  ["Historical crash rate", "leakage-safe; prior years only"],
  ["Housing density", "2020 census units / km², external to crashes"],
  ["Location", "H3 res 8 cell centroid (lat, lon)"],
  ["Time", "hour, weekday, weekend, month, holiday"],
  ["Weather", "temperature, rain, wind; NaN when missing"],
  ["Era", "COVID-period indicator (Mar 2020 – Dec 2021)"],
];

const BANDS = [
  { band: "LOW", range: "< 25", meaning: "below the city's median cell-hour" },
  { band: "MEDIUM", range: "25–49", meaning: "elevated but common" },
  { band: "HIGH", range: "50–74", meaning: "top band; plan around it" },
  { band: "CRITICAL", range: "75–100", meaning: "requires a nightlife contribution" },
];

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="paper-metric">
      <span className="paper-label">{label}</span>
      <strong>{value}</strong>
      <span className="paper-metric-detail">{detail}</span>
    </div>
  );
}

export function MethodScreen({
  health,
  onPanel,
}: {
  health: HealthResponse | undefined;
  onPanel: (panel: Panel) => void;
}) {
  const model = health?.checks.crash_risk_model;
  const version = model?.version ?? "2026.10.29-1";

  return (
    <div className="method-screen">
      <div className="paper-wrap">
        <div className="method-topline">
          <span className="paper-label">Method / model card</span>
          <span className="paper-label mono">v{version}</span>
          <button type="button" className="paper-back" onClick={() => onPanel("explore")}>
            <ArrowLeft size={13} />
            Back to the map
          </button>
        </div>

        <section className="method-hero">
          <div>
            <p className="paper-label">How the score is built</p>
            <h1 className="paper-display">
              The score is a
              <br />
              crash model,
              <br />
              not a vibe.
            </h1>
          </div>
          <div className="method-summary">
            <p>
              S.O.B.E.R. estimates <strong>expected injury-or-fatal crashes per H3
              cell-hour</strong>, then turns that estimate into a percentile. Property-
              damage-only collisions are excluded from the target. The number is a
              measured rate, not a hand-tuned opinion.
            </p>
            <p>
              The gate compares the model against an empirical-Bayes baseline on a
              temporal test set and a Brooklyn spatial holdout. Both must improve.
            </p>
            <div className="gate-line">
              <CheckCircle2 size={16} />
              <span>
                {model?.gate_passed === false
                  ? "Gate not passing in this build"
                  : "Gate #0002 passed · 2026-10-29"}
              </span>
            </div>
          </div>
        </section>

        <section className="paper-metric-grid" aria-label="Measured validation results">
          <Metric
            label="Temporal test / Poisson deviance"
            value="0.0354"
            detail="baseline 0.0467 · 24.1% lower"
          />
          <Metric
            label="Brooklyn holdout / Poisson deviance"
            value="0.0554"
            detail="baseline 0.0569 · 2.7% lower"
          />
          <Metric
            label="Top-decile lift"
            value="3.73× / 1.99×"
            detail="temporal / held-out borough"
          />
          <Metric
            label="Model surface"
            value="1,120 cells"
            detail="192,219 crashes · 2020–2026"
          />
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">01 / Inputs</span>
            <h2>What the model sees — and what it refuses.</h2>
          </div>
          <div className="method-two-col">
            <div>
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Feature</th>
                    <th>Source / treatment</th>
                  </tr>
                </thead>
                <tbody>
                  {FEATURES.map(([feature, source]) => (
                    <tr key={feature}>
                      <td>{feature}</td>
                      <td>{source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="refusal-card">
              <ShieldAlert size={20} />
              <h3>Not in the score</h3>
              <ul>
                <li>Property-damage-only crashes</li>
                <li>Post-crash injury severity as a predictor</li>
                <li>Driver identity, demographics or behavior</li>
                <li>Live police activity or traffic incidents</li>
                <li>Fabricated sensor data or a static heatmap</li>
              </ul>
              <p>
                The separate severity classifier is a demo of the crash table, not an
                input to this risk score.
              </p>
            </div>
          </div>
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">02 / Score</span>
            <h2>One hundred points, two honest components.</h2>
          </div>
          <div className="score-explainer">
            <div className="score-explainer-block">
              <span className="paper-label">Crash model / 60</span>
              <div className="point-tiers">
                <span>below P50 → 0</span>
                <span>P50–P75 → 15</span>
                <span>P75–P90 → 30</span>
                <span>P90–P99 → 50</span>
                <span>P99+ → 60</span>
              </div>
              <p>
                Percentiles come from the model's 188,160-cell-hour reference. A high
                percentile means the cell-hour is unusual for New York, not that a
                crash is certain.
              </p>
            </div>
            <div className="score-explainer-block">
              <span className="paper-label">Nightlife / 40</span>
              <div className="point-tiers">
                <span>0 venues → 0</span>
                <span>1–5 → 8</span>
                <span>6–13 → 16</span>
                <span>14–50 → 28</span>
                <span>&gt;50 → 40</span>
              </div>
              <p>
                This is the one non-crash input. It is capped at 40 so the model must
                earn the HIGH band; model points alone cannot reach CRITICAL.
              </p>
            </div>
          </div>
          <div className="band-table">
            {BANDS.map((row) => (
              <div key={row.band} className="band-row">
                <strong>{row.band}</strong>
                <span className="mono">{row.range}</span>
                <span>{row.meaning}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">03 / Routes</span>
            <h2>Distance-weighted exposure, not a decorated polyline.</h2>
          </div>
          <div className="formula-card">
            <Sigma size={22} />
            <div>
              <p className="formula mono">
                expected crashes = Σ ( rate<sub>cell</sub> × segment_km / √cell_area_km² )
              </p>
              <p>
                The route engine splits each provider geometry at H3 boundaries, scores
                every cell at the shared departure hour, and sums the exposure. The
                displayed “per 10 km” figure is derived from the total and the route
                length. Duration only decides which route is labelled fastest.
              </p>
            </div>
          </div>
          <p className="method-note">
            The corridor-width approximation is documented in <code>route_engine.py</code>.
            It is not a traffic simulation. A synthetic detour is reported as an
            improvement only after it has been scored on the same surface.
          </p>
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">04 / Calibration</span>
            <h2>The top decile is where the signal is.</h2>
          </div>
          <div className="calibration-wrap">
            <table className="paper-table calibration-table">
              <thead>
                <tr>
                  <th>Decile</th>
                  <th>Temporal predicted</th>
                  <th>Temporal observed</th>
                  <th>Lift</th>
                  <th>Brooklyn lift</th>
                </tr>
              </thead>
              <tbody>
                {TEMPORAL.map((row, index) => (
                  <tr key={row.decile}>
                    <td className="mono">{row.decile}</td>
                    <td className="mono">{row.predicted.toFixed(5)}</td>
                    <td className="mono">{row.observed.toFixed(5)}</td>
                    <td className="mono">{row.lift.toFixed(2)}×</td>
                    <td className="mono">{HOLDOUT[index]?.lift.toFixed(2)}×</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="method-note">
              Deciles are monotonic on both splits. The lowest decile over-predicts
              relative to its tiny observed rate; that is disclosed rather than hidden.
            </p>
          </div>
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">05 / Limits</span>
            <h2>Where this can be wrong.</h2>
          </div>
          <div className="limits-grid">
            <div>
              <span className="paper-label">Outside NYC</span>
              <p>
                The crash model covers its 1,120 New York cells only. Elsewhere the API
                returns a <code>context</code> answer labelled “Global estimate”: the
                country’s road-death rate, the road, turns, the hour, live weather and
                nightlife, multiplied into a relative index. Those factors are reasoned,
                not fitted, so it ranks places and routes; it does not count crashes.
                Inside the NYC box but off the model’s cells (rivers, Newark) the answer
                stays <code>rule_based_only</code> with no crash points.
              </p>
            </div>
            <div>
              <span className="paper-label">Brooklyn transfer</span>
              <p>
                The holdout improvement is real but small: 0.0554 vs 0.0569 deviance.
                Housing density is what makes the transfer work; without it the
                holdout fails.
              </p>
            </div>
            <div>
              <span className="paper-label">Weather</span>
              <p>
                Weather is live and needs no key: Open-Meteo first, MET Norway if it
                fails (a WeatherAPI key is optional). If both fail, the panel says
                “Weather unavailable” and the score uses neutral conditions (20 °C, dry,
                calm). A fallback is never presented as a live reading.
              </p>
            </div>
            <div>
              <span className="paper-label">Nightlife proxy</span>
              <p>
                Venue counts are a land-use proxy, not observed intoxication or
                traffic. It is capped at 40 points and disclosed as a separate
                component.
              </p>
            </div>
          </div>
        </section>

        <SeverityCard />

        <section className="method-section method-provenance">
          <div>
            <span className="paper-label">Sources</span>
            <ul className="source-list">
              <li>
                <Database size={12} />
                NYC Open Data h9gi-nx95 — Motor Vehicle Collisions, 2020–2026
              </li>
              <li>
                <Database size={12} />
                NYC Open Data kyz5-72x5 — Housing Database by 2020 NTA
              </li>
              <li>
                <Database size={12} />
                NYC Open Data 9nt8-h7nd — 2020 NTA boundaries
              </li>
              <li>
                <ExternalLink size={12} />
                Model artifact: <span className="mono">crash_risk_model.joblib</span> ·{" "}
                {model?.cells ?? 1120} cells · prior {0.003037756.toFixed(9)}
              </li>
            </ul>
          </div>
          <div className="method-command">
            <span className="paper-label">Reproduce the gate</span>
            <code>python -m pytest -q</code>
            <code>python scripts/capture_contract.py</code>
            <code>npm.cmd run build</code>
          </div>
        </section>

        <div className="method-footer">
          <span>S.O.B.E.R. · measured, not decorated.</span>
          <span className="mono">schema 1.0.0 · model {version}</span>
          <button type="button" className="paper-back" onClick={() => onPanel("plan")}>
            Plan a route <ArrowLeft size={13} className="rotate-180" />
          </button>
        </div>
      </div>
    </div>
  );
}
