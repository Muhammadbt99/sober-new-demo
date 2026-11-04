import { CheckCircle2, XCircle } from "lucide-react";

import { useGlobalModelCard } from "@/lib/queries";

const CHECK_LABEL: Record<string, string> = {
  beats_baseline: "Beats population-and-time in at least 4 of 5 held-out cities",
  mean_top_decile_lift: "Mean top-decile lift of at least 2.0",
  min_top_decile_lift: "Top-decile lift of at least 1.5 in every held-out city",
  mean_cell_spearman: "Mean rank correlation per cell of at least 0.6",
};

const TAG_LABEL: Record<string, string> = {
  signals: "traffic signals",
  crossings: "crossings",
  bus_stops: "bus stops",
  roundabouts: "roundabouts",
  stations: "stations",
  km_walk: "footways",
  km_cycle: "cycleways",
  nightlife: "bars and clubs",
  food: "restaurants and cafes",
  shops: "shops",
  schools: "schools",
  fuel: "fuel stations",
  parking: "car parks",
};

const FEATURE_SET_LABEL: Record<string, string> = {
  roads: "population, road length by class and junctions",
  mapped: "population, roads, junctions, signals, crossings, bus stops, roundabouts, stations, footways and cycleways",
  full: "population, the road network and mapped places (nightlife, food, shops, schools, fuel, parking)",
};

function fixed(value: number | null | undefined, digits: number) {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
}

/**
 * Method section 08: the global crash model used where New York's cannot be,
 * with every number read from /global_model.
 */
export function GlobalModelSection() {
  const card = useGlobalModelCard();
  const data = card.data;
  const cities = data ? Object.entries(data.training) : [];
  const region = data ? Object.values(data.regions)[0] : undefined;
  const names = cities.map(([, city]) => city.name);
  const cityList =
    names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0] ?? "several cities";

  return (
    <section className="method-section">
      <div className="method-section-head">
        <span className="paper-label">08 / Global crash model</span>
        <h2>Outside New York: learned from six cities, tested on places it never saw.</h2>
      </div>

      <div className="method-two-col">
        <div className="method-prose">
          <p>
            New York&apos;s model knows New York. For the UAE, S.O.B.E.R. uses a second model
            trained on the crash records of {cityList} (2021–2025). It sees only what exists everywhere: OpenStreetMap&apos;s road network
            for each 0.7 km² hex and its neighbours, Kontur population, and the hour and day of
            the week, counted from the start of the local weekend.
          </p>
          <p>
            Cities count crashes differently, so the target is each city&apos;s crashes{" "}
            <strong>relative to its own average</strong>. The model ranks places and hours; it
            never claims a crash count. A UAE score is the percentile of a hex-hour among the
            hexes Dubai Police record
            {region?.reference_cells
              ? ` (${region.reference_cells.toLocaleString()} of the UAE's ${region.cells.toLocaleString()})`
              : ""}{" "}
            at every hour of the week, the counterpart of New York&apos;s all-urban reference,
            on the same 0–60 tiers, plus nightlife counted from the same map. Against wider sets, every city street sat near the top at every hour.
          </p>
          <p>
            {data?.dubai_validation
              ? `Before Dubai joined the training data, the model scored ${data.dubai_validation.crashes.toLocaleString()} Dubai Police collisions from 2024-2025 that it had never seen: top-decile lift ${fixed(
                  data.dubai_validation.top_decile_lift,
                  2,
                )}, against ${fixed(data.dubai_validation.baseline_top_decile_lift ?? null, 2)} for population alone. Dubai then became a sixth training city.${
                  data.dubai_validation.retrain
                    ? ` On Dubai districts held out of training the lift is ${fixed(
                        data.dubai_validation.retrain.unseen_districts.after.top_decile_lift,
                        2,
                      )}, and on Dubai's 2026 records, which no model saw, ${fixed(
                        data.dubai_validation.retrain.untouched_2026.after.top_decile_lift,
                        2,
                      )}.`
                    : ""
                } Dubai is the only emirate that publishes these records, so the others are untested.`
              : "It has not been checked against UAE crash records yet. Until then the UAE numbers are a transfer from the cities below, and the panel says so."}
          </p>
        </div>
        <div className="refusal-card">
          <h3>Gate, decided before training</h3>
          <ul className="gate-checks">
            {Object.entries(CHECK_LABEL).map(([key, label]) => {
              const passed = data?.gate.checks[key];
              return (
                <li key={key}>
                  {passed === undefined ? null : passed ? (
                    <CheckCircle2 size={12} className="is-pass" aria-label="passed" />
                  ) : (
                    <XCircle size={12} className="is-fail" aria-label="failed" />
                  )}
                  <span>{label}</span>
                </li>
              );
            })}
          </ul>
          <p>
            Each city is held out in turn: trained on the others&apos; 2021–2023 crashes, scored
            on its own 2024–2025. The feature set was chosen earlier, on 2023.
          </p>
        </div>
      </div>

      {card.isLoading && <p className="method-note">Loading the global model card…</p>}
      {card.isError && (
        <p className="method-note">The global model card is not available from the API right now.</p>
      )}

      {data?.mapping_check && (
        <div className="method-prose method-aside">
          <p>
            <strong>Why the UAE gets the plain road model.</strong> On the original five cities the model
            does best with {FEATURE_SET_LABEL[data.best_on_training_cities ?? ""] ?? "more features"}.
            But those extra inputs depend on how thoroughly volunteers tagged a place, and per
            street junction the UAE has far fewer of them mapped than any training city:{" "}
            {Object.entries(data.mapping_check.features)
              .filter(([, row]) => !row.usable && row.share_of_least_mapped_city !== null)
              .sort((a, b) => (a[1].share_of_least_mapped_city ?? 0) - (b[1].share_of_least_mapped_city ?? 0))
              .slice(0, 4)
              .map(([feature, row]) => `${TAG_LABEL[feature] ?? feature} ${Math.round((row.share_of_least_mapped_city ?? 0) * 100)}%`)
              .join(", ")}{" "}
            of the least-mapped city. A model would read “not mapped” as “not there” and call
            those streets safer than they are. So the UAE is scored with{" "}
            {FEATURE_SET_LABEL[data.feature_set] ?? data.feature_set}, which are mapped as fully
            there as anywhere. That rule was fixed before the gate below was run.
          </p>
        </div>
      )}

      {data && (
        <>
          <table className="paper-table danger-table">
            <thead>
              <tr>
                <th>Held-out city</th>
                <th>Crashes 2024–25</th>
                <th>Top-decile lift: population → served model</th>
                <th>Rank correlation per hex</th>
                <th>Top 10% of hexes hold</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.gate.held_out).map(([key, row]) => {
                const base = data.gate.baseline[key];
                const name = data.training[key]?.name ?? key;
                return (
                  <tr key={key}>
                    <td>{name}</td>
                    <td className="mono">{row.crashes.toLocaleString()}</td>
                    <td className="mono">
                      {fixed(base?.top_decile_lift, 2)}× → {fixed(row.top_decile_lift, 2)}×
                    </td>
                    <td className="mono">
                      {fixed(base?.cell_spearman, 2)} → {fixed(row.cell_spearman, 2)}
                    </td>
                    <td className="mono">{Math.round(row.top10_cells_capture * 100)}% of crashes</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="method-note">
            Features: {FEATURE_SET_LABEL[data.feature_set] ?? data.feature_set}. Speed limits and
            lane counts are left out on purpose: they are mapped in some countries and not
            others, and a model would read their absence as a property of the road. Version{" "}
            <span className="mono">{data.version}</span>.
          </p>
        </>
      )}
    </section>
  );
}
