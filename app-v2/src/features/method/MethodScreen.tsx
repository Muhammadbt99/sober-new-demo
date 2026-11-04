import { ArrowLeft, CheckCircle2, Database, ExternalLink, ShieldAlert, Sigma } from "lucide-react";

import { DangerTypesSection } from "@/features/method/DangerTypesSection";
import { GlobalModelSection } from "@/features/method/GlobalModelSection";
import { SeverityCard } from "@/features/method/SeverityCard";
import type { HealthResponse, ModelCard } from "@/lib/contract";
import { useModelCard } from "@/lib/queries";
import type { Panel } from "@/lib/use-url-state";

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
  { band: "CRITICAL", range: "75–100", meaning: "needs nightlife, or the top 1% of crash risk in the UAE" },
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

const DASH = "—";

function fixed(value: number | undefined, digits: number) {
  return value === undefined ? DASH : value.toFixed(digits);
}

/** "24.1% lower" (model deviance against its baseline). */
function improvement(split: ModelCard["temporal_test"] | undefined) {
  if (!split) return DASH;
  const change = 1 - split.poisson_deviance / split.baseline_deviance;
  return `${Math.abs(change * 100).toFixed(1)}% ${change >= 0 ? "lower" : "higher"}`;
}

/** How far the split's predicted rates sit below (or above) what happened, overall. */
function levelGap(split: ModelCard["holdout"] | undefined) {
  if (!split || split.deciles.length === 0) return null;
  const predicted = split.deciles.reduce((sum, row) => sum + row.predicted_rate, 0);
  const observed = split.deciles.reduce((sum, row) => sum + row.observed_rate, 0);
  return observed > 0 ? predicted / observed - 1 : null;
}

function titleCase(value: string | undefined) {
  if (!value) return DASH;
  return value.charAt(0) + value.slice(1).toLowerCase();
}

export function MethodScreen({
  health,
  onPanel,
}: {
  health: HealthResponse | undefined;
  onPanel: (panel: Panel) => void;
}) {
  const model = health?.checks.crash_risk_model;
  const card = useModelCard().data;
  const version = card?.version ?? model?.version ?? DASH;
  const temporal = card?.temporal_test;
  const holdout = card?.holdout;
  const borough = titleCase(card?.holdout_borough);
  const trainedOn = card?.created_at?.slice(0, 10);
  const firstYear = card?.training_window.start.slice(0, 4);
  const lastYear = card?.training_window.end.slice(0, 4);
  const gap = levelGap(holdout);

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
              temporal test set and a {borough} spatial holdout. Both must improve.
            </p>
            <div className="gate-line">
              <CheckCircle2 size={16} />
              <span>
                {model?.gate_passed === false || card?.gate.passed === false
                  ? "Gate not passing in this build"
                  : trainedOn
                    ? `Gate passed · trained ${trainedOn}`
                    : "Gate passed"}
              </span>
            </div>
          </div>
        </section>

        <section className="paper-metric-grid" aria-label="Measured validation results">
          <Metric
            label="Temporal test / Poisson deviance"
            value={fixed(temporal?.poisson_deviance, 4)}
            detail={`baseline ${fixed(temporal?.baseline_deviance, 4)} · ${improvement(temporal)}`}
          />
          <Metric
            label={`${borough} holdout / Poisson deviance`}
            value={fixed(holdout?.poisson_deviance, 4)}
            detail={`baseline ${fixed(holdout?.baseline_deviance, 4)} · ${improvement(holdout)}`}
          />
          <Metric
            label="Top-decile lift"
            value={`${fixed(temporal?.top_decile_lift, 2)}× / ${fixed(holdout?.top_decile_lift, 2)}×`}
            detail="temporal / held-out borough"
          />
          <Metric
            label="Model surface"
            value={card ? `${card.cells.toLocaleString()} cells` : DASH}
            detail={
              card
                ? `${card.training_window.crashes.toLocaleString()} injury crashes · ${firstYear}–${lastYear}`
                : DASH
            }
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
                earn the HIGH band; model points alone cannot reach CRITICAL. The UAE is the
                exception: mapped nightlife there is too sparse, so the top 1% of crash risk
                scores at least 75 on its own.
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
                  <th>{borough} lift</th>
                </tr>
              </thead>
              <tbody>
                {(temporal?.deciles ?? []).map((row, index) => (
                  <tr key={row.decile}>
                    <td className="mono">{row.decile}</td>
                    <td className="mono">{row.predicted_rate.toFixed(5)}</td>
                    <td className="mono">{row.observed_rate.toFixed(5)}</td>
                    <td className="mono">{row.lift.toFixed(2)}×</td>
                    <td className="mono">{fixed(holdout?.deciles[index]?.lift, 2)}×</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!card && <p className="method-note">Loading the model card…</p>}
            <p className="method-note">
              {temporal?.monotonic_deciles && holdout?.monotonic_deciles
                ? "Deciles are monotonic on both splits. "
                : "Deciles are not monotonic on every split. "}
              {gap !== null && gap < -0.1
                ? `On the held-out borough the model ranks well but its rates run ${Math.round(
                    -gap * 100,
                  )}% below what happened: it learned the other boroughs’ level. The score is a percentile, so the ranking is what it uses.`
                : null}
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
                The crash model covers its {card?.cells.toLocaleString() ?? DASH} New York
                cells only. In the UAE the global crash model (section 08) answers. Anywhere
                else the API returns a <code>context</code> answer labelled “Global
                estimate”: the country’s road-death rate, the road, turns, the hour, live
                weather and nightlife, multiplied into a relative index. Those factors are
                reasoned, not fitted, so it ranks places and routes; it does not count
                crashes. Inside the NYC box but off the model’s cells (rivers, Newark) the
                answer stays <code>rule_based_only</code> with no crash points.
              </p>
            </div>
            <div>
              <span className="paper-label">{borough} transfer</span>
              <p>
                The holdout improvement is real but small:{" "}
                {fixed(holdout?.poisson_deviance, 4)} vs {fixed(holdout?.baseline_deviance, 4)}{" "}
                deviance. Housing density is what makes the transfer work; without it the
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

        <DangerTypesSection />

        <GlobalModelSection />

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">09 / When to leave</span>
            <h2>The same route, at the hours around your departure.</h2>
          </div>
          <div className="limits-grid">
            <div>
              <span className="paper-label">What it does</span>
              <p>
                Plan re-scores the chosen route at each whole hour, three hours either way.
                The model changes by the hour, so finer slots would be false precision. The
                planned hour reproduces the route card&apos;s number, to within rounding.
              </p>
            </div>
            <div>
              <span className="paper-label">When it stays quiet</span>
              <p>
                A new time is suggested only for a cut of at least 15%, a design choice and
                not a statistical test, and only if it also beats the city&apos;s own change at
                that hour. There are no traffic counts here, so the citywide change stands in
                for how busy the hour is. After a time is taken the window stays on the original
                plan, so suggestions cannot walk a trip later. Trips outside a crash model get no chart, because the
                estimate&apos;s time curve is assumed, not learned.
              </p>
            </div>
            <div>
              <span className="paper-label">Why not 03:00</span>
              <p>
                The model counts crashes per place and hour, not per car. Few crashes in the
                small hours mostly means few cars, and that is when impaired and drowsy crashes
                peak, so 00:00 to 05:59 is shown but never suggested.
              </p>
            </div>
            <div>
              <span className="paper-label">Held constant</span>
              <p>
                Drive time and weather stay as planned at every hour. The whole trip is scored
                at its departure hour, as the route cards are, even when a long drive runs into
                the next hours.
              </p>
            </div>
          </div>
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">10 / Driver check-in</span>
            <h2>What the driver brings, set against what the road records show.</h2>
          </div>
          <div className="limits-grid">
            <div>
              <span className="paper-label">What it does</span>
              <p>
                Plan asks about hours awake, a night shift and alcohol. The answers point to the
                drowsy and impaired crash groups, and a warning names the stretches of the chosen
                route where that kind of crash runs at twice a typical hour or more. The twice is
                a design choice.
              </p>
            </div>
            <div>
              <span className="paper-label">Why 17 hours</span>
              <p>
                After 17 to 19 hours without sleep, performance on some tests was equivalent to or
                worse than at a blood alcohol level of 0.05% (Williamson and Feyer, Occupational
                and Environmental Medicine, 2000).
              </p>
            </div>
            <div>
              <span className="paper-label">What it never does</span>
              <p>
                It does not change the risk score or put a number on one driver&apos;s risk: no
                data here could support one. It is self-reported, and not a medical or legal
                finding. Crash-type records cover New York only, so elsewhere it rests on the
                answers alone.
              </p>
            </div>
            <div>
              <span className="paper-label">Privacy</span>
              <p>
                The answers stay in the page&apos;s memory. They are not saved, and they are not
                sent to the API or to any other service.
              </p>
            </div>
          </div>
        </section>

        <section className="method-section">
          <div className="method-section-head">
            <span className="paper-label">11 / Get home safe</span>
            <h2>After dark, the trip as a plan instead of a list of routes.</h2>
          </div>
          <div className="limits-grid">
            <div>
              <span className="paper-label">What it does</span>
              <p>
                From 20:00 to 05:59, Plan puts the trip in order, and by day a button shows the
                same trip tonight. It scans every road for the trip, whichever one is selected,
                and picks the same road as the recommendation, with a reason for each one it
                turns down. Then it walks through the drive: turn by turn, with each risky part
                flagged where you reach it, then a place to stop and a calmer place to be picked
                up. It adds no estimates: each line restates a number scored elsewhere.
              </p>
            </div>
            <div>
              <span className="paper-label">Pickup point</span>
              <p>
                The nearest map cell within 1 km ranking at least 15 percentile points lower than
                the start. Both limits are design choices. Cells are about 1 km across, so this is
                a direction to walk, not an address. The distance is a straight line, and the rank
                is about crashes on those roads, not personal safety on foot.
              </p>
            </div>
            <div>
              <span className="paper-label">If you should not drive</span>
              <p>
                Saying so in the check-in drops the driving steps. That happens in the browser, so
                the answer is never sent to the server.
              </p>
            </div>
          </div>
        </section>

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
                {(card?.cells ?? model?.cells)?.toLocaleString() ?? DASH} cells · prior{" "}
                {fixed(card?.prior_rate, 6)}
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
