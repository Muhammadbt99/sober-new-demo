import * as maplibregl from "maplibre-gl";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  CornerUpLeft,
  Flag,
  Pause,
  Play,
  RefreshCw,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { LABEL_LAYERS } from "@/map/basemap-style";
import type { Route } from "@/lib/contract";
import {
  buildPath,
  headingAt,
  nextStep,
  pointAt,
  riskNotice,
  riskZones,
  spokenDistance,
  turnShape,
  turnTowards,
  type TurnShape,
} from "@/lib/drive";
import type { Point, TimeParams } from "@/lib/api";
import { dangerAt } from "@/lib/checkin";
import { useGuidance } from "@/lib/queries";
import { cn } from "@/lib/utils";

import "./drive.css";

export type DriveTrip = {
  route: Route;
  routeIndex: number;
  origin: Point;
  destination: Point;
  time: TimeParams;
  /** Kinds of crash the driver's check-in points to. */
  driverGroups: string[];
};

const SPEEDS = [
  { label: "10×", factor: 10, zoom: 16.6, pitch: 60 },
  { label: "60×", factor: 60, zoom: 14.6, pitch: 58 },
  { label: "300×", factor: 300, zoom: 12.8, pitch: 50 },
];
const WARN_RANGE_M = 1500;
const BUILDINGS = "drive-buildings";
const HUD_INTERVAL_MS = 150;

function TurnArrow({ shape }: { shape: TurnShape }) {
  const size = 34;
  if (shape === "left") return <ArrowLeft size={size} aria-hidden="true" />;
  if (shape === "right") return <ArrowRight size={size} aria-hidden="true" />;
  if (shape === "uturn") return <CornerUpLeft size={size} aria-hidden="true" />;
  if (shape === "roundabout") return <RefreshCw size={size} aria-hidden="true" />;
  if (shape === "arrive") return <Flag size={size} aria-hidden="true" />;
  return <ArrowUp size={size} aria-hidden="true" />;
}

function clockAt(time: number): string {
  const at = new Date(time);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

function duration(minutes: number): string {
  const whole = Math.max(0, Math.round(minutes));
  if (whole < 60) return `${whole} min`;
  return `${Math.floor(whole / 60)} h ${whole % 60} min`;
}

/**
 * A simulated drive along a route already chosen: the camera sits behind an
 * arrow, with the next turn and the risk ahead on screen.
 *
 * Nothing here is live on the road. The car moves at the route's average speed,
 * sped up, and every number comes from the route as planned.
 */
export function DriveMode({
  map,
  trip,
  hiddenLayers,
  onExit,
}: {
  map: maplibregl.Map;
  trip: DriveTrip;
  hiddenLayers: string[];
  onExit: () => void;
}) {
  const { route } = trip;
  const path = useMemo(() => buildPath(route.geometry), [route.geometry]);
  const zones = useMemo(() => riskZones(route.segments, path.total), [route.segments, path.total]);
  const guidance = useGuidance(trip.origin, trip.destination, trip.time, trip.routeIndex);
  const steps = useMemo(
    () => (guidance.data?.available ? (guidance.data.instructions ?? []) : []),
    [guidance.data],
  );
  const stepScale = guidance.data?.length_m ? path.total / guidance.data.length_m : 1;

  const minutes = route.duration_min ?? 0;
  const metresPerSecond = minutes > 0 ? path.total / (minutes * 60) : 14;

  const [metres, setMetres] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speedIndex, setSpeedIndex] = useState(() => (path.total > 150_000 ? 2 : path.total > 40_000 ? 1 : 0));
  const live = useRef({ metres: 0, playing: true, speedIndex, bearing: 0, ready: false });
  useEffect(() => {
    live.current.playing = playing;
    live.current.speedIndex = speedIndex;
  }, [playing, speedIndex]);
  // Fixed when the drive starts: leaving now means arriving then, however fast
  // the simulation plays.
  const [arrival] = useState(() => Date.now() + minutes * 60_000);
  const finished = metres >= path.total - 1;

  useEffect(() => {
    const speed = SPEEDS[live.current.speedIndex]!;
    const start = pointAt(path, 0);
    const bearing = headingAt(path, 0, 60);
    live.current.bearing = bearing;

    for (const id of hiddenLayers) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
    }
    if (map.getSource("overture") && !map.getLayer(BUILDINGS)) {
      map.addLayer(
        {
        id: BUILDINGS,
        type: "fill-extrusion",
        source: "overture",
        "source-layer": "building",
        minzoom: 14,
        paint: {
          "fill-extrusion-color": "#8f99a8",
          // A guess of two storeys where a source gives no height: a low box
          // beside the road reads as a building, a tall guess would invent one.
          "fill-extrusion-height": [
            "coalesce",
            ["get", "height"],
            ["*", ["coalesce", ["get", "num_floors"], 2], 3.2],
          ],
          "fill-extrusion-base": ["coalesce", ["get", "min_height"], 0],
          "fill-extrusion-opacity": 0.72,
        },
        },
        LABEL_LAYERS[0],
      );
    }
    map.dragPan.disable();
    map.scrollZoom.disable();
    map.doubleClickZoom.disable();
    map.touchZoomRotate.disable();
    map.keyboard.disable();

    const element = document.createElement("div");
    element.className = "drive-car";
    const marker = new maplibregl.Marker({
      element,
      rotationAlignment: "map",
      pitchAlignment: "map",
    })
      .setLngLat([start[1], start[0]])
      .setRotation(bearing)
      .addTo(map);

    const padding = () => ({ top: map.getContainer().clientHeight * 0.42, bottom: 0, left: 0, right: 0 });
    map.stop();
    map.easeTo({
      center: [start[1], start[0]],
      zoom: speed.zoom,
      pitch: speed.pitch,
      bearing,
      padding: padding(),
      duration: 1600,
      essential: true,
    });
    const settle = window.setTimeout(() => {
      live.current.ready = true;
    }, 1650);

    let frame = 0;
    let last = performance.now();
    let lastHud = 0;
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const seconds = Math.min(0.1, (now - last) / 1000);
      last = now;
      const state = live.current;
      if (!state.ready) return;
      const setting = SPEEDS[state.speedIndex]!;
      if (state.playing && state.metres < path.total) {
        state.metres = Math.min(path.total, state.metres + metresPerSecond * setting.factor * seconds);
      }
      const here = pointAt(path, state.metres);
      const travelled = metresPerSecond * setting.factor;
      const target = headingAt(path, state.metres, Math.max(45, travelled * 1.2));
      state.bearing = turnTowards(state.bearing, target, 1 - Math.exp(-seconds * 3.5));
      marker.setLngLat([here[1], here[0]]).setRotation(state.bearing);
      map.jumpTo({
        center: [here[1], here[0]],
        bearing: state.bearing,
        zoom: map.getZoom() + (setting.zoom - map.getZoom()) * Math.min(1, seconds * 2),
        pitch: map.getPitch() + (setting.pitch - map.getPitch()) * Math.min(1, seconds * 2),
        padding: padding(),
      });
      if (now - lastHud > HUD_INTERVAL_MS) {
        lastHud = now;
        setMetres(state.metres);
      }
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
      marker.remove();
      if (map.getLayer(BUILDINGS)) map.removeLayer(BUILDINGS);
      for (const id of hiddenLayers) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
      }
      map.dragPan.enable();
      map.scrollZoom.enable();
      map.doubleClickZoom.enable();
      map.touchZoomRotate.enable();
      map.keyboard.enable();
      const bounds = new maplibregl.LngLatBounds();
      for (const [lat, lon] of path.points) bounds.extend([lon, lat]);
      map.stop();
      map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
      const camera = map.cameraForBounds(bounds, {
        padding: { top: 90, bottom: 150, left: 440, right: 90 },
        bearing: 0,
      });
      map.easeTo({ ...camera, pitch: 0, bearing: 0, duration: 1000, essential: true });
    };
  }, [hiddenLayers, map, metresPerSecond, path]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onExit();
      if (event.key === " ") {
        event.preventDefault();
        setPlaying((value) => !value);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onExit]);

  const next = nextStep(steps, metres, stepScale);
  const notice = riskNotice(zones, metres, WARN_RANGE_M);
  const driver = dangerAt(route.segments, path.total, metres, trip.driverGroups);
  const left = Math.max(0, 1 - metres / (path.total || 1));
  const minutesLeft = minutes * left;

  return (
    <div className="drive-hud">
      <div className="drive-top">
        <div className="drive-step" aria-live="polite">
          {finished ? (
            <>
              <span className="drive-step-icon">
                <Flag size={34} aria-hidden="true" />
              </span>
              <div>
                <strong>You have arrived</strong>
                <span>{duration(minutes)} · {(path.total / 1000).toFixed(1)} km in this simulation</span>
              </div>
            </>
          ) : next ? (
            <>
              <span className="drive-step-icon">
                <TurnArrow shape={turnShape(next.step.maneuver)} />
              </span>
              <div>
                <strong>In {spokenDistance(next.distance_m)}</strong>
                <span>
                  {turnShape(next.step.maneuver) === "arrive"
                    ? "Your destination is ahead"
                    : next.step.message}
                </span>
              </div>
            </>
          ) : (
            <>
              <span className="drive-step-icon">
                <ArrowUp size={34} aria-hidden="true" />
              </span>
              <div>
                <strong>{spokenDistance(path.total - metres)} to go</strong>
                <span>
                  {guidance.isLoading
                    ? "Getting directions…"
                    : guidance.data?.reason ?? "Turn directions are not available for this trip"}
                </span>
              </div>
            </>
          )}
        </div>

        {notice && !finished && (
          <div
            className={cn("drive-risk", notice.zone.level === "CRITICAL" && "is-critical")}
            role="status"
          >
            <TriangleAlert size={20} aria-hidden="true" />
            <div>
              <strong>
                {notice.kind === "inside"
                  ? `${notice.zone.level === "CRITICAL" ? "Critical" : "High"} risk for ${spokenDistance(notice.distance_m)}`
                  : `${notice.zone.level === "CRITICAL" ? "Critical" : "High"}-risk stretch in ${spokenDistance(notice.distance_m)}`}
              </strong>
              {notice.zone.reason && <span>{notice.zone.reason}</span>}
            </div>
          </div>
        )}
      </div>

      {driver && !finished && (
        <div className="drive-driver">
          <div className="drive-risk is-driver" role="status">
            <TriangleAlert size={20} aria-hidden="true" />
            <div>
              <strong>
                {driver.group === "impaired" ? "You said you have had alcohol" : "You said you are tired"}
              </strong>
              <span>
                {driver.group === "impaired" ? "Impaired-driver" : "Drowsy-driver"} crashes run{" "}
                {driver.ratio.toFixed(1)}× a typical hour here
              </span>
            </div>
          </div>
        </div>
      )}

      <div className="drive-bottom">
        <div className="drive-track" aria-hidden="true">
          <span style={{ width: `${(1 - left) * 100}%` }} />
        </div>
        <div className="drive-bar">
          <div className="drive-figures">
            <strong>{duration(minutesLeft)}</strong>
            <span>
              {((path.total - metres) / 1000).toFixed(1)} km · arrive {clockAt(arrival)}
            </span>
          </div>
          <span className="drive-tag">Simulation</span>
          {trip.driverGroups.length > 0 && <span className="drive-checkin">Check-in on</span>}
          <div className="drive-controls">
            <div className="drive-speeds" role="group" aria-label="Simulation speed">
              {SPEEDS.map((speed, index) => (
                <button
                  key={speed.label}
                  type="button"
                  className={cn(index === speedIndex && "is-active")}
                  aria-pressed={index === speedIndex}
                  onClick={() => setSpeedIndex(index)}
                >
                  {speed.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="drive-button"
              onClick={() => setPlaying((value) => !value)}
              aria-label={playing ? "Pause" : "Resume"}
              disabled={finished}
            >
              {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
            </button>
            <button type="button" className="drive-button is-exit" onClick={onExit}>
              <X size={16} aria-hidden="true" />
              End
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
