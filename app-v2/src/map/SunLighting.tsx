import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import {
  angularDistance,
  destination,
  subsolarPoint,
  type LngLat,
} from "@/map/sun";

type SunLightingProps = {
  map: MapLibreMap | null;
  /**
   * The instant the sun is drawn for, in epoch milliseconds: the real clock in
   * live mode, or the time-dock hour when the sun follows the dock.
   */
  sunTime: number;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

/**
 * Degrees the sun's *placement* is rotated toward the top of the frame,
 * independent of the light direction. Stylistic: keeps the disc out of the
 * band where the legend and map controls sit.
 */
const SUN_TILT = (16 * Math.PI) / 180;

/**
 * The overlay is clipped to the globe's bounding box instead of covering the
 * viewport. A full-screen pass costs ~7M fragments per frame at 1920x901; the
 * disc costs a few hundred thousand, which is the difference between 49 fps and
 * a locked 60 while the planet rotates.
 */
const MAX_BACKING_PX = 900;

/** Initial bearing from `from` to `to`, in degrees clockwise from north. */
function bearingBetween(from: LngLat, to: LngLat): number {
  const φ1 = (from.lat * Math.PI) / 180;
  const φ2 = (to.lat * Math.PI) / 180;
  const dλ = ((to.lon - from.lon) * Math.PI) / 180;
  const y = Math.sin(dλ) * Math.cos(φ2);
  const x =
    Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(dλ);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

const VERTEX_SHADER = `
attribute vec2 aPos;
void main() {
  gl_Position = vec4(aPos, 0.0, 1.0);
}
`;

/**
 * A sphere lit by a directional light, which is exactly what a flat overlay of
 * gradients cannot fake: the terminator of an orthographic sphere is a conic,
 * not a circle and not a straight line, and it changes shape as the sun swings
 * from "behind the camera" to "behind the planet".
 *
 * For a fragment at normalised offset p inside the disc, the surface normal is
 *
 *     n = (p.x, p.y, sqrt(1 - |p|^2))
 *
 * so a single dot product against the sun direction in view space gives the
 * true cosine falloff. The result is the *night* alpha only; the day side is
 * left alone so the basemap keeps its own colour.
 *
 * The canvas is sized to the disc and positioned over it, so `uCenter` is its
 * middle. `gl_FragCoord` is bottom-up, which is the same frame the light
 * vector uses: +y is up the screen.
 */
const FRAGMENT_SHADER = `
precision mediump float;

uniform vec2 uCenter;
uniform float uRadius;
uniform vec3 uLight;
uniform vec3 uNight;
uniform float uStrength;

void main() {
  vec2 p = (gl_FragCoord.xy - uCenter) / uRadius;

  float r2 = dot(p, p);
  if (r2 >= 1.0) {
    gl_FragColor = vec4(0.0);
    return;
  }

  vec3 normal = vec3(p, sqrt(1.0 - r2));
  float ndl = dot(normal, uLight);

  // Soft twilight band rather than a hard edge: the atmosphere scatters, so
  // the boundary reaches a little way into the night.
  float lit = smoothstep(-0.10, 0.34, ndl);
  float night = (1.0 - lit) * uStrength;

  gl_FragColor = vec4(uNight, night);
}
`;

function compile(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/**
 * Sun and terminator for the globe.
 *
 * MapLibre cannot light a globe: its `light` style property only shades
 * extruded geometry, and the engine ships no sun, subsolar or terminator code.
 * So the lighting is drawn over the canvas:
 *
 *   - a WebGL pass darkens the hemisphere the sun is not above, using the real
 *     sphere normal, so the terminator is geometrically correct;
 *   - a distant sun sits far outside the planet on the true solar bearing.
 *
 * Both follow the hour in the time dock, so the globe agrees with the number on
 * screen: 23:00 really is night over New York.
 *
 * The sky half fades out as soon as the planet stops floating in frame, and the
 * whole overlay is gone by street zoom so the risk hexes keep full contrast.
 */
export function SunLighting({ map, sunTime }: SunLightingProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sunRef = useRef<HTMLDivElement | null>(null);
  const sunTimeRef = useRef(sunTime);
  const drawRef = useRef<(() => void) | null>(null);

  // Hour changes only repaint the existing GL context. Rebuilding the program
  // on every scrub tick used to recompile both shaders.
  useEffect(() => {
    sunTimeRef.current = sunTime;
    drawRef.current?.();
  }, [sunTime]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const sunEl = sunRef.current;
    if (!map || !canvas || !sunEl) return;

    const gl = canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: false,
      antialias: false,
      depth: false,
      stencil: false,
    });
    if (!gl) return;

    const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
    const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
    const program = gl.createProgram();
    if (!vertex || !fragment || !program) return;

    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    // One oversized triangle covers the viewport with no seam.
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW,
    );
    const aPos = gl.getAttribLocation(program, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const uCenter = gl.getUniformLocation(program, "uCenter");
    const uRadius = gl.getUniformLocation(program, "uRadius");
    const uLight = gl.getUniformLocation(program, "uLight");
    const uNight = gl.getUniformLocation(program, "uNight");
    const uStrength = gl.getUniformLocation(program, "uStrength");

    gl.clearColor(0, 0, 0, 0);

    let raf = 0;
    let cachedTime = Number.NaN;
    let cachedSun: LngLat = { lat: 0, lon: 0 };
    let lastSize = 0;
    let lastLeft = Number.NaN;
    let lastTop = Number.NaN;
    let visible = false;
    // Cached, because reading clientWidth after writing styles forces layout
    // on every animation frame.
    let stageW = 0;
    let stageH = 0;

    const measure = () => {
      const stage = map.getContainer();
      stageW = stage.clientWidth;
      stageH = stage.clientHeight;
    };
    measure();

    const setVisible = (next: boolean) => {
      if (visible === next) return;
      visible = next;
      canvas.style.visibility = next ? "visible" : "hidden";
    };

    const draw = () => {
      const width = stageW;
      const height = stageH;
      if (!width || !height) return;

      const zoom = map.getZoom();
      const center = map.getCenter();
      const view: LngLat = { lat: center.lat, lon: center.lng };
      const centerPx = map.project(center);

      // A point 89 degrees away lands on the silhouette, so its screen distance
      // is the globe radius for this zoom and viewport.
      const edgeBearing = Math.abs(center.lat) > 70 ? 180 : 90;
      const edgePx = map.project(destination(view, edgeBearing, 89));
      const radius = Math.hypot(edgePx.x - centerPx.x, edgePx.y - centerPx.y);

      const minDim = Math.min(width, height);
      // How much empty sky is left around the planet: 1 while it floats in
      // frame, 0 once its limb runs off the edge of the viewport.
      const framing = clamp((minDim * 0.5 - radius) / Math.max(60, minDim * 0.1), 0, 1);
      const skyStrength = framing * clamp((5.5 - zoom) / 1.2, 0, 1);
      // Day and night themselves are drawn inside the map now (daynight.ts), under
      // the data. This pass only adds the curve of a lit sphere: a light shadow
      // that deepens toward the night limb.
      const earthStrength = clamp((5.2 - zoom) / 1.4, 0, 1) * 0.32;

      if (!Number.isFinite(radius) || radius < 40) {
        setVisible(false);
        sunEl.style.opacity = "0";
        return;
      }

      // ---- light direction, in view space --------------------------------
      // Recomputed only when the sun's instant changes, not every frame.
      if (sunTimeRef.current !== cachedTime) {
        cachedSun = subsolarPoint(new Date(sunTimeRef.current));
        cachedTime = sunTimeRef.current;
      }
      const theta = (angularDistance(cachedSun, view) * Math.PI) / 180;
      const bearing = (bearingBetween(view, cachedSun) * Math.PI) / 180;
      const sinTheta = Math.sin(theta);
      const cosTheta = Math.cos(theta);
      const light: [number, number, number] = [
        Math.sin(bearing) * sinTheta,
        Math.cos(bearing) * sinTheta,
        cosTheta,
      ];

      // ---- distant sun ----------------------------------------------------
      // Held well clear of the limb, on the solar bearing, and pulled in only
      // as far as it must be to stay inside the frame. A disc parked next to
      // the planet reads as a sticker on it; distance is what sells "star".
      //
      // The placement bearing is tilted a little toward the top of the frame so
      // the sun sits in open sky instead of level with the equator, where the
      // legend and controls live. The light itself keeps the true bearing.
      const skyBearing = bearing - SUN_TILT;
      const skyX = Math.sin(skyBearing);
      const skyY = -Math.cos(skyBearing);

      const margin = 96;
      let maxDist = Number.POSITIVE_INFINITY;
      if (skyX > 1e-3) maxDist = Math.min(maxDist, (width - margin - centerPx.x) / skyX);
      if (skyX < -1e-3) maxDist = Math.min(maxDist, (margin - centerPx.x) / skyX);
      if (skyY > 1e-3) maxDist = Math.min(maxDist, (height - margin - centerPx.y) / skyY);
      if (skyY < -1e-3) maxDist = Math.min(maxDist, (margin - centerPx.y) / skyY);

      const dist = Math.min(radius * 2.45, Math.max(0, maxDist));
      const sunVisible = dist > radius * 1.45 ? 1 : 0;

      const glowSize = radius * 0.78;
      const sunX = centerPx.x + skyX * dist;
      const sunY = centerPx.y + skyY * dist;

      sunEl.style.width = `${glowSize}px`;
      sunEl.style.height = `${glowSize}px`;
      sunEl.style.transform = `translate(${sunX - glowSize / 2}px, ${
        sunY - glowSize / 2
      }px)`;
      sunEl.style.opacity = String(sunVisible * skyStrength);

      // At street zoom neither surface is visible: skip the GL pass entirely
      // instead of clearing and compositing an empty full-screen layer.
      if (earthStrength <= 0.002) {
        setVisible(false);
        return;
      }

      // ---- terminator, clipped to the disc --------------------------------
      const size = radius * 2;
      // Soft gradient, so a capped backing store is invisible but keeps the
      // fragment count bounded on high-DPI projector screens.
      const dpr = Math.min(window.devicePixelRatio || 1, 2, MAX_BACKING_PX / size);
      const backing = Math.max(1, Math.round(size * dpr));

      if (canvas.width !== backing || canvas.height !== backing) {
        canvas.width = backing;
        canvas.height = backing;
      }
      if (size !== lastSize) {
        canvas.style.width = `${size}px`;
        canvas.style.height = `${size}px`;
        lastSize = size;
      }
      const left = centerPx.x - radius;
      const top = centerPx.y - radius;
      if (left !== lastLeft || top !== lastTop) {
        canvas.style.transform = `translate(${left}px, ${top}px)`;
        lastLeft = left;
        lastTop = top;
      }
      setVisible(true);

      gl.viewport(0, 0, backing, backing);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(uCenter, backing / 2, backing / 2);
      gl.uniform1f(uRadius, backing / 2);
      gl.uniform3f(uLight, light[0], light[1], light[2]);
      gl.uniform3f(uNight, 0.043, 0.072, 0.13);
      gl.uniform1f(uStrength, earthStrength);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        draw();
      });
    };

    const onResize = () => {
      measure();
      schedule();
    };

    drawRef.current = draw;
    draw();
    map.on("move", schedule);
    map.on("resize", onResize);

    return () => {
      drawRef.current = null;
      map.off("move", schedule);
      map.off("resize", onResize);
      if (raf) cancelAnimationFrame(raf);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
    };
  }, [map]);

  return (
    <>
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0"
        style={{ visibility: "hidden" }}
      />
      <div
        ref={sunRef}
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0 rounded-full"
        style={{
          opacity: 0,
          mixBlendMode: "screen",
          background:
            "radial-gradient(circle, #ffffff 0%, #fffdf6 4%, rgba(255,248,220,0.9) 6.5%, rgba(255,232,170,0.34) 12%, rgba(255,206,126,0.08) 26%, rgba(255,182,96,0.02) 48%, transparent 70%)",
        }}
      />
    </>
  );
}
