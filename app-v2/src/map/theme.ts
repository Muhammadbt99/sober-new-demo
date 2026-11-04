import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";

import { basemapStyle } from "@/map/basemap-style";

export type MapTheme = "light" | "dark";

/**
 * The daylight palette, as a translation of the night palette.
 *
 * `basemap-style.ts` is authored dark. Every colour in it has a daylight
 * counterpart here, keyed by the dark value, so one table re-themes all 32
 * layers (planet and NYC extract alike) without a second copy of the style.
 * Roles stay consistent: water stays darker than land, as it does on paper
 * maps, and roads are drawn as white lines on grey casings.
 */
const DAY: Record<string, string> = {
  // land: background and the bundled world outline, by zoom
  "#4a5a3c": "#c8d4ad",
  "#33473a": "#d8decb",
  "#152f3f": "#ebe7de",
  "#0d2f45": "#f1eee8",
  "#54663f": "#c3d0a4",
  "#3a5140": "#d6ddc5",
  // coast and borders
  "#d3f0f9": "#6f9cc1",
  "#6fc2d8": "#86b0d0",
  "#5f93ad": "#a49cbb",
  "#3b5164": "#a8a0bd",
  // sea and rivers
  "#15517d": "#8fc3e6",
  "#0d3857": "#a7d0ec",
  "#061a2b": "#b9dbf1",
  "#071a28": "#b9dbf1",
  "#0a2c42": "#9fcbe9",
  // landcover
  "#2f6137": "#a8cf95",
  "#0c3b2a": "#c5dfb3",
  "#56763c": "#c1daa5",
  "#11462f": "#d4e7c1",
  "#a6874f": "#ead9b0",
  "#6a5a38": "#eee3c7",
  "#eaf6fc": "#ffffff",
  "#c9e2ee": "#f3f8fb",
  "#0c3124": "#cfe6c0",
  "#0c1119": "#e5e1da",
  // land in the NYC extract, buildings
  "#0f2334": "#f1eee8",
  "#0e1824": "#e2dcd2",
  // Overture buildings, their outlines, and school or hospital grounds
  "#1b2a3b": "#d3cabb",
  "#2d4058": "#b8ad9b",
  "#2a2f45": "#eadff0",
  // labels: road and place names, water names, landmarks, then the halo behind them
  "#d9e3ee": "#2f3a47",
  "#8fc1e0": "#2f6f9f",
  "#b6c2d0": "#5a6573",
  "#0a1320": "#ffffff",
  // roads: casing, then motorway to service
  "#03121c": "#cdc6ba",
  "#49657f": "#fbe8b8",
  "#334b62": "#ffffff",
  "#24374a": "#ffffff",
  "#1a2838": "#fbfaf7",
  "#141f2c": "#f5f3ee",
};

function translate(value: unknown, table: Record<string, string>): unknown {
  if (typeof value === "string") return table[value.toLowerCase()] ?? value;
  if (Array.isArray(value)) return value.map((item) => translate(item, table));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, translate(item, table)]),
    );
  }
  return value;
}

function mentionsColour(value: unknown): boolean {
  if (typeof value === "string") return value.startsWith("#");
  if (Array.isArray(value)) return value.some(mentionsColour);
  return false;
}

/** The basemap style in a given theme. Used to construct the map. */
export function themedStyle(theme: MapTheme): StyleSpecification {
  if (theme === "dark") return basemapStyle;
  return {
    ...basemapStyle,
    layers: basemapStyle.layers.map(
      (layer) =>
        translate(layer, DAY) as (typeof basemapStyle.layers)[number],
    ),
  };
}

/**
 * Re-colour a live map's basemap layers. Only colour properties are touched,
 * so it is a few dozen `setPaintProperty` calls and no style reload.
 */
export function applyBasemapTheme(map: MapLibreMap, theme: MapTheme): void {
  for (const layer of basemapStyle.layers) {
    if (!("paint" in layer) || !layer.paint || !map.getLayer(layer.id)) continue;
    for (const [property, value] of Object.entries(layer.paint)) {
      if (!mentionsColour(value)) continue;
      map.setPaintProperty(
        layer.id,
        // Keys come straight from the style's own paint objects.
        property as Parameters<MapLibreMap["setPaintProperty"]>[1],
        theme === "dark" ? value : translate(value, DAY),
      );
    }
  }
}
