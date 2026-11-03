import { BookOpen, Map, Play, Route, Square } from "lucide-react";

import { AlertsPopover } from "@/components/AlertsPopover";
import { StatusPopover } from "@/components/StatusPopover";
import { Wordmark } from "@/components/Wordmark";
import type { HealthResponse } from "@/lib/contract";
import type { Panel } from "@/lib/use-url-state";

type TopBarProps = {
  panel: Panel;
  onPanel: (panel: Panel) => void;
  present: boolean;
  onPresent: () => void;
  health: {
    data?: HealthResponse;
    isLoading: boolean;
    isError: boolean;
  };
};

const NAV: Array<{ id: Panel; label: string; icon: typeof Map }> = [
  { id: "explore", label: "Explore", icon: Map },
  { id: "plan", label: "Plan", icon: Route },
  { id: "method", label: "Method", icon: BookOpen },
];

export function TopBar({ panel, onPanel, present, onPresent, health }: TopBarProps) {
  const model = health.data?.checks.crash_risk_model;
  const tone = health.isError || model?.available === false ? "bad" : model?.available ? "ok" : "warn";
  const status = health.isLoading
    ? "Connecting"
    : health.isError
      ? "API offline"
      : model?.available
        ? "Model live"
        : "Model unavailable";

  return (
    <header className="topbar">
      <Wordmark />

      <nav className="nav-pill" aria-label="Primary">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              data-active={panel === item.id}
              aria-current={panel === item.id ? "page" : undefined}
              onClick={() => onPanel(item.id)}
            >
              <Icon size={12} strokeWidth={1.8} aria-hidden="true" />
              <span className="nav-label">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="top-actions">
        <AlertsPopover />
        <StatusPopover health={health} label={status} tone={tone} />
        <button
          type="button"
          className={present ? "solid-button" : "ghost-button"}
          onClick={onPresent}
          aria-pressed={present}
        >
          {present ? <Square size={12} /> : <Play size={12} />}
          <span>{present ? "Exit present" : "Present"}</span>
        </button>
      </div>
    </header>
  );
}
