import type { LngLat } from "@/map/sun";

/**
 * Day and night on the map itself.
 *
 * The night side is a shadow painted into a world-sized texture and drawn as a
 * raster layer *inside* the map, above the basemap and below the risk hexes and
 * routes. So the data stays at full strength while the land and sea under it
 * go dark, and because the shadow is geographic it turns with the globe.
 */

const RAD = Math.PI / 180;

/** Web Mercator's latitude limit; the texture spans exactly this band. */
export const MERCATOR_MAX_LAT = 85.051129;

/** Corner coordinates for a world-covering image source (TL, TR, BR, BL). */
export const WORLD_COORDINATES: [[number, number], [number, number], [number, number], [number, number]] = [
  [-180, MERCATOR_MAX_LAT],
  [180, MERCATOR_MAX_LAT],
  [180, -MERCATOR_MAX_LAT],
  [-180, -MERCATOR_MAX_LAT],
];

/** Colour of the night shadow (deep navy, matching the dark theme's sea). */
const NIGHT_RGB: [number, number, number] = [4, 11, 24];

/** Opacity of full night. Below 1 so coastlines still read on the dark side. */
const NIGHT_MAX_ALPHA = 0.8;

/** Elevation of the sun above the horizon at a point, in degrees. */
export function sunElevation(lat: number, lon: number, sun: LngLat): number {
  const s =
    Math.sin(lat * RAD) * Math.sin(sun.lat * RAD) +
    Math.cos(lat * RAD) * Math.cos(sun.lat * RAD) * Math.cos((lon - sun.lon) * RAD);
  return Math.asin(Math.min(1, Math.max(-1, s))) / RAD;
}

/**
 * How dark a place is, 0 (day) to 1 (night), from the sun's elevation.
 * Clear above +1 degree, fully dark below -14 (past nautical twilight), with a
 * smooth ramp through dusk so the terminator is a soft band, not a line.
 */
export function nightAmount(elevationDeg: number): number {
  const t = Math.min(1, Math.max(0, (1 - elevationDeg) / 15));
  return t * t * (3 - 2 * t);
}

/**
 * Paint the night shadow for a sun position into `canvas`.
 *
 * Rows are spaced in Web Mercator, not in latitude, because an image source is
 * stretched linearly between its corners in Mercator space. Using latitude
 * rows would put the terminator hundreds of kilometres off at high latitudes.
 * Cost: one multiply-add per pixel after per-row and per-column trig, ~3 ms.
 */
export function paintNightTexture(
  canvas: HTMLCanvasElement,
  sun: LngLat,
  width = 512,
  height = 512,
): void {
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return;
  const image = context.createImageData(width, height);
  const data = image.data;

  const sinDecl = Math.sin(sun.lat * RAD);
  const cosDecl = Math.cos(sun.lat * RAD);
  const cosHour = new Float64Array(width);
  for (let x = 0; x < width; x += 1) {
    const lon = -180 + ((x + 0.5) / width) * 360;
    cosHour[x] = Math.cos((lon - sun.lon) * RAD);
  }

  for (let y = 0; y < height; y += 1) {
    const mercY = Math.PI * (1 - (2 * (y + 0.5)) / height);
    const lat = Math.atan(Math.sinh(mercY));
    const a = Math.sin(lat) * sinDecl;
    const b = Math.cos(lat) * cosDecl;
    let index = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      const elevation = Math.asin(Math.min(1, Math.max(-1, a + b * cosHour[x]!))) / RAD;
      data[index] = NIGHT_RGB[0];
      data[index + 1] = NIGHT_RGB[1];
      data[index + 2] = NIGHT_RGB[2];
      data[index + 3] = Math.round(nightAmount(elevation) * NIGHT_MAX_ALPHA * 255);
      index += 4;
    }
  }
  context.putImageData(image, 0, 0);
}
