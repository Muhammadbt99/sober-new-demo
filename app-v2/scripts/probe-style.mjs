// Read OpenFreeMap's own style and report the road/water layers it defines.
// This is the tile producer's authoritative class vocabulary for these tiles.
const STYLE = "https://tiles.openfreemap.org/styles/liberty";
const res = await fetch(STYLE);
const style = await res.json();
console.log("style:", style.name, "| sources:", Object.keys(style.sources).join(", "));

const want = ["transportation", "water", "landcover", "landuse", "building", "boundary", "park", "waterway"];
const seen = new Map();
for (const layer of style.layers) {
  const sl = layer["source-layer"];
  if (!sl || !want.includes(sl)) continue;
  if (layer.type === "symbol") continue;
  if (!seen.has(sl)) seen.set(sl, []);
  seen.get(sl).push(layer);
}

for (const [sl, layers] of seen) {
  console.log(`\n=== ${sl} (${layers.length} non-symbol layers)`);
  for (const l of layers) {
    let filter = l.filter;
    // Collect class names mentioned anywhere in the filter.
    const classes = new Set();
    const walk = (node) => {
      if (Array.isArray(node)) {
        if (node[0] === "get" && node[1] === "class") return;
        for (const n of node) walk(n);
      } else if (typeof node === "string" && node !== "class" && !["match", "in", "==", "!=", "literal", "get", "has", "all", "any", "none", "!in"].includes(node)) {
        classes.add(node);
      }
    };
    if (filter) walk(filter);
    const mn = l.minzoom ?? 0;
    const mx = l.maxzoom ?? style.maxzoom ?? 22;
    console.log(`  ${l.id.padEnd(34)} z${String(mn).padStart(2)}-${String(mx).padEnd(2)} ${l.type.padEnd(5)} ${classes.size ? "class in: " + [...classes].join(",") : ""}`);
  }
}
