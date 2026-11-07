import { useEffect } from "react";
import { ArrowLeft, ArrowRight, X } from "lucide-react";

import { cn } from "@/lib/utils";

export type PresentStep = {
  id: string;
  title: string;
  caption: string;
};

export const PRESENT_STEPS: PresentStep[] = [
  {
    id: "surface",
    title: "Live risk surface",
    caption: "New York at Saturday 23:00. The hexes are model percentiles, not a static heatmap.",
  },
  {
    id: "scenario",
    title: "Load the judged scenario",
    caption: "Times Square to Brooklyn Borough Hall. One control fills both ends and the hour.",
  },
  {
    id: "routes",
    title: "Fastest vs safest",
    caption: "The comparison is crash exposure, not average risk score.",
  },
  {
    id: "segment",
    title: "Explain the worst segment",
    caption: "Click the hottest cell on the route; the score breaks into model and nightlife.",
  },
  {
    id: "time",
    title: "Change the hour",
    caption: "The same route at 03:00. The model is time-aware; the surface moves.",
  },
  {
    id: "method",
    title: "Show the measured gate",
    caption: "Temporal test, Brooklyn holdout, calibration deciles, and stated limitations.",
  },
  {
    id: "outside",
    title: "Be honest outside NYC",
    caption: "Newark is outside the model. No crash points are invented; coverage is rule_based_only.",
  },
  {
    id: "uae",
    title: "Beyond New York: the UAE",
    caption:
      "Downtown Dubai, scored by the global crash model: learned from six cities including Dubai, tested on Dubai districts and a year it never saw.",
  },
  {
    id: "night",
    title: "Get home safe",
    caption:
      "Times Square at Saturday 23:00: a plan, not just a route. Every line comes from the scored trip, and nothing is invented.",
  },
];

export function PresentBar({
  step,
  onStep,
  onExit,
}: {
  step: number;
  onStep: (step: number) => void;
  onExit: () => void;
}) {
  const current = PRESENT_STEPS[step] ?? PRESENT_STEPS[0]!;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (event.key === "ArrowRight" || event.key === " ") {
        event.preventDefault();
        onStep(Math.min(PRESENT_STEPS.length - 1, step + 1));
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        onStep(Math.max(0, step - 1));
      } else if (event.key === "Escape") {
        onExit();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onExit, onStep, step]);

  return (
    <div className="present-rail panel" role="region" aria-label="Present mode">
      <div className="present-index mono">
        {String(step + 1).padStart(2, "0")}
        <span>/</span>
        {String(PRESENT_STEPS.length).padStart(2, "0")}
      </div>
      <div className="present-copy">
        <span className="eyebrow">Present mode · arrow keys advance</span>
        <strong>{current.title}</strong>
        <p>{current.caption}</p>
      </div>
      <div className="present-controls">
        <button
          type="button"
          className="icon-button"
          onClick={() => onStep(Math.max(0, step - 1))}
          disabled={step === 0}
          aria-label="Previous step"
        >
          <ArrowLeft size={13} />
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={() => onStep(Math.min(PRESENT_STEPS.length - 1, step + 1))}
          disabled={step === PRESENT_STEPS.length - 1}
          aria-label="Next step"
        >
          <ArrowRight size={13} />
        </button>
        <button type="button" className="icon-button" onClick={onExit} aria-label="Exit present mode">
          <X size={13} />
        </button>
      </div>
      <div className="present-dots" aria-hidden="true">
        {PRESENT_STEPS.map((item, index) => (
          <button
            key={item.id}
            type="button"
            className={cn("present-dot", index === step && "is-active")}
            onClick={() => onStep(index)}
            tabIndex={-1}
          />
        ))}
      </div>
    </div>
  );
}
