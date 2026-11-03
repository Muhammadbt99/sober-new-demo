# world-countries.geojson

Offline country outlines used as the low-zoom globe backdrop.

- Source: `world-atlas@2` `countries-110m.json` (TopoJSON), fetched from
  `https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json`
- Converted with `topojson-client@3` (`feature()`), properties reduced to `name`
- Coordinates rounded to 3 decimal places (about 100 m at the equator; the
  source is 1:110m so this loses no visible detail)
- Output: 177 features, 191,320 bytes, minified GeoJSON FeatureCollection

Regeneration sketch (run outside the app, no runtime dependency is added):

```js
import fs from "node:fs";
import { feature } from "topojson-client";

const topo = JSON.parse(fs.readFileSync("countries-110m.json", "utf8"));
const fc = feature(topo, topo.objects.countries);
const round = (v) =>
  typeof v[0] === "number"
    ? [Math.round(v[0] * 1000) / 1000, Math.round(v[1] * 1000) / 1000]
    : v.map(round);
const out = {
  type: "FeatureCollection",
  features: fc.features.map((f) => ({
    type: "Feature",
    properties: { name: f.properties?.name ?? "" },
    geometry: { type: f.geometry.type, coordinates: round(f.geometry.coordinates) },
  })),
};
fs.writeFileSync("world-countries.geojson", JSON.stringify(out));
```
