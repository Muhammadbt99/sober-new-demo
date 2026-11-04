import type { ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";

import { LoadingBlock } from "@/components/DataState";
import type { HealthResponse } from "@/lib/contract";
import { useStats } from "@/lib/queries";
import { RISK_COLORS, formatHour } from "@/lib/risk";
import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "bad";

function Row({ label, value, tone }: { label: string; value: ReactNode; tone?: Tone }) {
  return (
    <div className="status-row">
      <dt>{label}</dt>
      <dd>
        {tone && <span className="status-dot" data-tone={tone} aria-hidden="true" />}
        {value}
      </dd>
    </div>
  );
}

const PROVIDER_NAMES: Record<string, string> = {
  weatherapi: "WeatherAPI",
  "open-meteo": "Open-Meteo",
  "met-norway": "MET Norway",
  photon: "Photon",
  nominatim: "Nominatim",
  "overpass-europe": "Overpass Europe",
  "overpass-britain": "Overpass Britain",
  overpass: "Overpass",
  "overpass-coffee": "Overpass (private.coffee)",
  "overpass-vk": "Overpass (VK)",
};

function providerName(id: string): string {
  return PROVIDER_NAMES[id.trim()] ?? id.trim();
}

/** "open-meteo -> met-norway" as "Open-Meteo → MET Norway". */
function chainLabel(chain: string | undefined): string {
  return (chain ?? "").split("->").map(providerName).filter(Boolean).join(" → ");
}

function indexTone(value: string): Tone {
  return value === "ok" ? "ok" : value === "offline" ? "warn" : "bad";
}

function Usage() {
  const stats = useStats();
  if (stats.isLoading) return <LoadingBlock lines={3} />;
  if (stats.isError || !stats.data) {
    return <p className="section-note">Usage figures are not available.</p>;
  }
  const { total_requests: totals, risk_level_breakdown: bands } = stats.data;
  const bandTotal = Object.values(bands).reduce((sum, value) => sum + value, 0) || 1;
  const hour = stats.data.most_dangerous_hour.hour;

  return (
    <>
      <dl className="status-list">
        <Row label="Risk checks" value={<span className="mono">{totals.risk}</span>} />
        <Row label="Route plans" value={<span className="mono">{totals.routes}</span>} />
        <Row
          label="Average score"
          value={<span className="mono">{stats.data.average_risk_score.toFixed(1)}</span>}
        />
        <Row
          label="Busiest danger hour"
          value={<span className="mono">{hour === null ? "—" : formatHour(hour)}</span>}
        />
      </dl>
      <div className="band-split" role="img" aria-label="Share of checks by risk band">
        {Object.entries(bands).map(([band, value]) => (
          <span
            key={band}
            title={`${band}: ${value}`}
            style={{
              flexGrow: value / bandTotal,
              background: RISK_COLORS[band as keyof typeof RISK_COLORS] ?? "#8190a2",
            }}
          />
        ))}
      </div>
      <p className="section-note">{stats.data.privacy_note}</p>
    </>
  );
}

/**
 * The "Model live" chip, made useful. It was a decoration; now it opens the
 * whole system picture: both models, the data indexes, the routing chain, what
 * the API exposes, and the anonymous usage log.
 */
export function StatusPopover({
  health,
  label,
  tone,
}: {
  health: { data?: HealthResponse; isLoading: boolean; isError: boolean };
  label: string;
  tone: Tone | "warn";
}) {
  const data = health.data;
  const crash = data?.checks.crash_risk_model;
  const providers = data?.checks.providers;
  const chain = providers?.routing_chain.split(" -> ") ?? [];

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className="status-chip status-chip-button" aria-label={`System status: ${label}`}>
          <span className="status-dot" data-tone={tone} />
          {label}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="popover-card popover-wide" sideOffset={10} align="end" collisionPadding={12}>
          <div className="popover-head">
            <div>
              <span className="eyebrow">System status</span>
              <h2>{label}</h2>
            </div>
            {data && <span className="chip">{data.endpoint_count} endpoints</span>}
          </div>

          {health.isLoading && <LoadingBlock lines={4} />}
          {health.isError && (
            <p className="section-note">
              The API is not reachable. Start the Flask server on port 5000.
            </p>
          )}

          {data && crash && providers && (
            <div className="popover-stack">
              <section>
                <h3 className="popover-section">Models</h3>
                <dl className="status-list">
                  <Row
                    label="Crash-risk model"
                    tone={crash.available ? "ok" : "bad"}
                    value={
                      <>
                        <span className="mono">v{crash.version ?? "?"}</span>
                        <span>{crash.gate_passed ? "gate passed" : crash.status}</span>
                      </>
                    }
                  />
                  <Row label="Modelled cells" value={<span className="mono">{crash.cells.toLocaleString()}</span>} />
                  {data.checks.global_model && (
                    <Row
                      label="Global crash model"
                      tone={data.checks.global_model.available ? "ok" : "warn"}
                      value={
                        data.checks.global_model.available ? (
                          <>
                            <span className="mono">{data.checks.global_model.version}</span>
                            <span>
                              {Object.values(data.checks.global_model.regions ?? {})
                                .map((region) => `${region.name} · ${region.cells.toLocaleString()} cells`)
                                .join(", ")}
                            </span>
                          </>
                        ) : (
                          data.checks.global_model.error ?? "unavailable"
                        )
                      }
                    />
                  )}
                  <Row
                    label="Severity model"
                    tone={data.checks.severity_model === "ok" ? "ok" : "warn"}
                    value={`${data.checks.severity_model} · research only`}
                  />
                </dl>
              </section>

              <section>
                <h3 className="popover-section">Data</h3>
                <dl className="status-list">
                  <Row label="Crash index" tone={indexTone(data.checks.indexes.crash)} value={data.checks.indexes.crash} />
                  <Row label="Nightlife index" tone={indexTone(data.checks.indexes.bars)} value={data.checks.indexes.bars} />
                  <Row label="Safe stops" tone={indexTone(data.checks.indexes.safe_stops)} value={data.checks.indexes.safe_stops} />
                </dl>
              </section>

              <section>
                <h3 className="popover-section">Live providers</h3>
                <dl className="status-list">
                  <Row
                    label="Weather"
                    tone={
                      providers.weather_last
                        ? "ok"
                        : providers.weather_last === null
                          ? "warn"
                          : providers.weather_api_key === "missing" ? "warn" : "ok"
                    }
                    value={
                      providers.weather_last
                        ? `${providerName(providers.weather_last)} · live`
                        : providers.weather_last === null
                          ? "no reading yet"
                          : providers.weather_api_key === "missing" ? "no key · fallback" : "connected"
                    }
                  />
                  {providers.weather_chain && (
                    <Row label="Weather order" value={chainLabel(providers.weather_chain)} />
                  )}
                  {providers.places && (
                    <Row
                      label="Places & roads"
                      value={chainLabel(providers.places.replace(" (OpenStreetMap)", ""))}
                    />
                  )}
                  {typeof providers.nightlife === "string" && (
                    <Row label="Nightlife counts" value={chainLabel(providers.nightlife)} />
                  )}
                  {providers.context_model && (
                    <Row label="Outside NYC" value={`global estimate · ${providers.context_model.split(" ")[0]}`} />
                  )}
                </dl>
                {providers.provider_state &&
                  Object.values(providers.provider_state).some((state) => state !== "ok") && (
                    <p className="section-note">
                      Skipping for now:{" "}
                      {Object.entries(providers.provider_state)
                        .filter(([, state]) => state !== "ok")
                        .map(([name, state]) => `${providerName(name)} (${state})`)
                        .join(", ")}
                      . The next provider in line answers instead.
                    </p>
                  )}
                <p className="section-note">
                  All keyless and free. Each is tried in order; one that keeps failing is
                  paused for a minute so it cannot slow the map down.
                </p>
              </section>

              <section>
                <h3 className="popover-section">Routing chain</h3>
                <ol className="chain">
                  {chain.map((step, index) => {
                    const disabled = step.includes("(disabled)");
                    return (
                      <li key={step} className={cn(disabled && "is-disabled")}>
                        <span className="mono">{index + 1}</span>
                        {step.replace(" (disabled)", "")}
                      </li>
                    );
                  })}
                </ol>
                <p className="section-note">
                  Tried in order; the first that answers wins. Extra alternatives come from
                  the OSRM servers.
                </p>
              </section>

              <section>
                <h3 className="popover-section">Coverage</h3>
                <dl className="status-list">
                  <Row label="Model area" value={data.coverage.model_area} />
                  <Row
                    label="Bounding box"
                    value={<span className="mono">{data.coverage.bounding_box.join(", ")}</span>}
                  />
                </dl>
              </section>

              <section>
                <h3 className="popover-section">Live usage</h3>
                <Usage />
              </section>
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
