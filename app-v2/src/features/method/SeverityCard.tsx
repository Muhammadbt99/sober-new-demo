import { FlaskConical } from "lucide-react";

import { ML_IMAGES } from "@/lib/api";
import { useMlStats } from "@/lib/queries";

const CLASS_LABELS: Record<string, string> = {
  property_damage_only: "Property damage only",
  injury_likely: "Injury likely",
  fatal_risk: "Fatal",
};

const pct = (value: number | undefined, digits = 1) =>
  value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
const fixed = (value: number | undefined, digits = 3) =>
  value === undefined ? "—" : value.toFixed(digits);

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="paper-metric">
      <span className="paper-label">{label}</span>
      <strong>{value}</strong>
      <span className="paper-metric-detail">{detail}</span>
    </div>
  );
}

/**
 * The severity classifier's own model card, read live from `/ml/stats` and the
 * two chart endpoints. Everything here comes from the API; nothing is typed in,
 * so the page cannot drift from the model that is actually loaded.
 */
export function SeverityCard() {
  const query = useMlStats();
  const stats = query.data;
  // Read from the API so the caption cannot claim something the chart no longer shows.
  const topFeatures = Object.entries(stats?.feature_importance ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([name]) => name);

  return (
    <section className="method-section">
      <div className="method-section-head">
        <span className="paper-label">06 / Severity classifier</span>
        <h2>A second model, kept out of the score on purpose.</h2>
      </div>

      {query.isLoading && <p className="paper-note">Loading the model card…</p>}
      {query.isError && (
        <p className="paper-note">
          The severity model card comes from <code>/ml/stats</code>, which this API did not
          return. The risk score does not depend on it.
        </p>
      )}

      {stats && (
        <>
          <p className="paper-note">
            <FlaskConical size={13} aria-hidden="true" /> {stats.model ?? "Classifier"} v
            {stats.version ?? "?"} · {stats.status ?? "research only"}
          </p>

          <div className="paper-metric-grid" aria-label="Severity model validation">
            <Metric
              label="Macro-F1"
              value={fixed(stats.macro_f1)}
              detail={`majority-class baseline ${fixed(stats.baseline_macro_f1)}`}
            />
            <Metric
              label="ROC AUC (macro)"
              value={fixed(stats.roc_auc_macro)}
              detail="0.5 is chance"
            />
            <Metric
              label="Cross-validated accuracy"
              value={pct(stats.cross_validation_accuracy)}
              detail={`± ${((stats.cv_std ?? 0) * 100).toFixed(2)} pts · balanced ${pct(stats.balanced_accuracy)}`}
            />
            <Metric
              label="Fatal PR-AUC"
              value={fixed(stats.fatal_pr_auc)}
              detail={`fatal is ${pct(stats.dataset?.class_share?.fatal_risk, 2)} of crashes`}
            />
          </div>

          {stats.ablation_context_only && (
            <p className="paper-note">
              With only time and place — what a pre-crash risk model could know — macro-F1 falls
              from {fixed(stats.macro_f1)} to {fixed(stats.ablation_context_only.macro_f1)} and
              fatal recall to {pct(stats.ablation_context_only.fatal_recall, 0)}. That gap is why
              this model describes crashes instead of predicting them.
            </p>
          )}

          <div className="method-two-col">
            <div>
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Outcome</th>
                    <th>Share of crashes</th>
                    <th>Precision</th>
                    <th>Recall</th>
                  </tr>
                </thead>
                <tbody>
                  {(stats.classes ?? Object.keys(CLASS_LABELS)).map((id) => (
                    <tr key={id}>
                      <td>{CLASS_LABELS[id] ?? id}</td>
                      <td className="mono">{pct(stats.dataset?.class_share?.[id], 1)}</td>
                      <td className="mono">{pct(stats.per_class_precision?.[id], 1)}</td>
                      <td className="mono">{pct(stats.per_class_recall?.[id], 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {stats.dataset && (
                <p className="paper-note">
                  {stats.dataset.rows_used?.toLocaleString() ?? "—"} crashes
                  {stats.dataset.date_range
                    ? `, ${stats.dataset.date_range[0]} to ${stats.dataset.date_range[1]}`
                    : ""}
                  . {stats.dataset.source}.
                </p>
              )}
            </div>

            <div className="refusal-card">
              <FlaskConical size={20} aria-hidden="true" />
              <h3>What it is not</h3>
              <ul>
                {(stats.honesty ?? []).slice(0, 4).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              {stats.calibration?.note && <p>{stats.calibration.note}</p>}
            </div>
          </div>

          <div className="method-figures">
            <figure>
              <img
                src={ML_IMAGES.confusionMatrix}
                alt="Confusion matrix of the severity model on held-out crashes"
                loading="lazy"
              />
              <figcaption>
                Confusion matrix, held-out crashes. Rows are what happened; columns are what the
                model said.
              </figcaption>
            </figure>
            <figure>
              <img
                src={ML_IMAGES.featureImportance}
                alt="Permutation importance of the severity model's features"
                loading="lazy"
              />
              <figcaption>
                How much macro-F1 drops when a feature is shuffled
                {topFeatures.length > 0 && (
                  <>
                    {" "}
                    — most influential here: <code>{topFeatures.join(", ")}</code>
                  </>
                )}
                .
              </figcaption>
            </figure>
          </div>

          {stats.limitations && stats.limitations.length > 0 && (
            <ul className="paper-bullets">
              {stats.limitations.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
