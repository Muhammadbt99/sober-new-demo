import * as Popover from "@radix-ui/react-popover";
import { Bell, CloudOff, ShieldCheck } from "lucide-react";

import { ErrorBlock, LoadingBlock, Notice } from "@/components/DataState";
import { useAlerts } from "@/lib/queries";
import { weatherLabel } from "@/lib/risk";

function severityTone(severity: string): string {
  const level = severity.toLowerCase();
  if (level.includes("sever") || level.includes("high") || level.includes("critical")) return "bad";
  if (level.includes("moder") || level.includes("warn") || level.includes("medium")) return "warn";
  return "ok";
}

/**
 * The city-wide weather alert feed (`/alerts`). The Explore panel only ever
 * showed the alerts for the selected point; this is the whole watch list.
 */
export function AlertsPopover() {
  const alerts = useAlerts();
  const count = alerts.data?.total_alerts ?? 0;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          className="icon-button top-icon-button"
          aria-label={count > 0 ? `Weather alerts: ${count} active` : "Weather alerts"}
        >
          <Bell size={14} aria-hidden="true" />
          {count > 0 && <span className="top-icon-badge">{count}</span>}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="popover-card" sideOffset={10} align="end" collisionPadding={12}>
          <div className="popover-head">
            <div>
              <span className="eyebrow">Weather watch</span>
              <h2>City alerts</h2>
            </div>
            {alerts.data && (
              <span className="chip">{alerts.data.monitored_locations} locations</span>
            )}
          </div>

          {alerts.isLoading && <LoadingBlock lines={3} />}
          {alerts.isError && (
            <ErrorBlock
              error={alerts.error}
              onRetry={() => void alerts.refetch()}
              title="Alerts unavailable"
            />
          )}

          {alerts.data && (
            <div className="popover-stack">
              {alerts.data.alerts.length === 0 ? (
                <div className="alerts-empty">
                  <ShieldCheck size={16} aria-hidden="true" />
                  <div>
                    <strong>No active alerts</strong>
                    <span>Nothing in the watch list needs attention right now.</span>
                  </div>
                </div>
              ) : (
                <ul className="alert-list">
                  {alerts.data.alerts.map((alert, index) => (
                    <li key={`${alert.type}-${alert.location ?? index}`} data-tone={severityTone(alert.severity)}>
                      <strong>
                        {alert.type.replaceAll("_", " ")}
                        {alert.location ? ` · ${alert.location}` : ""}
                      </strong>
                      <span>{alert.message}</span>
                    </li>
                  ))}
                </ul>
              )}

              {alerts.data.warning && (
                <Notice tone="warn">
                  <CloudOff size={11} aria-hidden="true" /> {alerts.data.warning}
                </Notice>
              )}

              <dl className="popover-facts">
                <div>
                  <dt>Last checked</dt>
                  <dd className="mono">{alerts.data.last_checked ?? "not yet"}</dd>
                </div>
                <div>
                  <dt>Next check</dt>
                  <dd>{alerts.data.next_check}</dd>
                </div>
                <div>
                  <dt>Weather data</dt>
                  <dd>{weatherLabel(alerts.data.data_quality)}</dd>
                </div>
              </dl>
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
