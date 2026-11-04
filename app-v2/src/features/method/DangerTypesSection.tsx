import { CheckCircle2, XCircle } from "lucide-react";

import { DANGER_LAYERS, dangerMeta } from "@/lib/danger";
import { useDangerCard } from "@/lib/queries";

const LEVEL_LABEL: Record<string, string> = {
  time: "time of week only",
  borough: "borough × time",
  neighbourhood: "neighbourhood × time",
};

function percent(value: number | undefined) {
  if (value === undefined) return "—";
  return `${(value * 100).toFixed(value < 0.01 ? 2 : 1)}%`;
}

/**
 * Method section 07: how danger types are estimated and how they were gated.
 * Every number comes from the trained artifact through /danger_types.
 */
export function DangerTypesSection() {
  const card = useDangerCard();
  const data = card.data;

  return (
    <section className="method-section">
      <div className="method-section-head">
        <span className="paper-label">07 / Danger types</span>
        <h2>What kind of crash, measured rather than guessed.</h2>
      </div>

      <div className="method-two-col">
        <div className="method-prose">
          <p>
            Police record contributing factors (alcohol, a driver asleep, unsafe speed)
            <strong> after</strong> a crash, so the crash model never sees them. Here they are the
            <strong> target</strong>: the share of injury crashes in an area and band of the week
            whose recorded factors include each kind. The only inputs are where (the
            neighbourhood or borough) and when (six bands of the week), both known before a trip.
          </p>
          <p>
            Impaired and drowsy crashes are rare, so shares are pooled and each small sample is
            pulled toward the level above it (neighbourhood → borough → city) by an amount tuned
            on 2025. The level each kind is served at was chosen on 2025 and scored once on
            2026. The expected rate of a kind is the crash model’s rate times its share, compared
            with a typical New York cell-hour. None of it changes the 0–100 score.
          </p>
        </div>
        <div className="refusal-card">
          <h3>Gate, decided before training</h3>
          <ul>
            <li>Beats one citywide share on 2026 (log-loss)</li>
            <li>Top 10% of predictions show at least 1.5× the average rate</li>
            <li>A neighbourhood or borough level must also beat time-only</li>
            <li>Fewer than 5 expected recorded crashes behind an estimate: “not enough data”</li>
          </ul>
          <p>A kind that fails is switched off and listed here, not hidden.</p>
        </div>
      </div>

      {card.isLoading && <p className="method-note">Loading the danger-type model card…</p>}
      {card.isError && (
        <p className="method-note">
          The danger-type model card is not available from the API right now.
        </p>
      )}

      {data && (
        <>
          <table className="paper-table danger-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Share of injury crashes</th>
                <th>Served at</th>
                <th>2026 lift: time only → served</th>
                <th>2026 log-loss vs city</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.groups)
                .sort(
                  ([a, specA], [b, specB]) =>
                    Number(specA.status !== "validated") - Number(specB.status !== "validated") ||
                    (DANGER_LAYERS as readonly string[]).indexOf(a) -
                      (DANGER_LAYERS as readonly string[]).indexOf(b),
                )
                .map(([id, spec]) => {
                const served = spec.test[spec.level];
                const city = spec.test.city;
                return (
                  <tr key={id}>
                    <td>{dangerMeta(id).label}</td>
                    <td className="mono">{percent(data.city_share[id])}</td>
                    <td>{LEVEL_LABEL[spec.level] ?? spec.level}</td>
                    <td className="mono">
                      {spec.test.time.top_decile_lift?.toFixed(2)}× → {served.top_decile_lift?.toFixed(2)}×
                    </td>
                    <td className="mono">
                      {served.log_loss.toFixed(4)} vs {city.log_loss.toFixed(4)}
                    </td>
                    <td className={spec.status === "validated" ? "gate-pass" : "gate-fail"}>
                      {spec.status === "validated" ? (
                        <>
                          <CheckCircle2 size={12} /> validated
                        </>
                      ) : (
                        <>
                          <XCircle size={12} /> switched off
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="method-note">
            {data.data.injury_crashes.toLocaleString()} injury crashes,{" "}
            {data.data.window[0]?.slice(0, 10)} to {data.data.window[1]?.slice(0, 10)}: train{" "}
            {data.data.split.train.toLocaleString()}, validation{" "}
            {data.data.split.validation.toLocaleString()}, held-out 2026 test{" "}
            {data.data.split.test.toLocaleString()}. About half of all factor entries are
            “unspecified”, so shares describe recorded factors: they rank places and hours, they
            do not count drunk or tired drivers. The 2026 test holds few drowsy crashes, so that
            kind’s lift is the least certain. Version <span className="mono">{data.version}</span>.
          </p>
        </>
      )}
    </section>
  );
}
