# Shaduw langs de snelweg

Shade detection at roadside stops (EU) from open satellite data. For stopping
places along highways (rest areas, service stations, parkings) the pipeline
estimates **how shaded each stop is**, including seasonal variation — think of
a parked car with a baby in it on a summer afternoon.

Design and rationale: [shade-detection-pipeline-plan.md](shade-detection-pipeline-plan.md).

## How it works

Two complementary signals per stop (Sentinel-2 only sees ~10:30 shadows, so
shadows alone are not enough):

1. **Detected shadow** — dark, cloud-free pixels in seasonal Sentinel-2 L2A
   median composites (AWS earth-search STAC, no account needed).
2. **Modeled shadow** — canopy height (Meta/WRI global canopy height, 1 m COG
   tiles on AWS Open Data; alternatively ETH 2020 10 m or a local GeoTIFF) +
   sun position (`pvlib`) → ray-marched shadow footprint for any date/time,
   e.g. 15:00 on the summer solstice (the headline metric).

Seasonal NDVI (summer vs. winter) additionally shows whether trees are
deciduous — shade in summer, sun in winter, exactly what you want.

Everything is combined into a 0–1 `shade_score` and a `none`/`partial`/`good`
class, written as **GeoParquet** + **GeoPackage** + a **Folium HTML map**.

## Usage

```bash
pixi install

# run the prototype (5 Dutch rest areas)
pixi run shaduw run --config configs/nl-prototype.toml

# or explicitly
pixi run shaduw run --config configs/nl-prototype.toml --limit 1 --force

# discover all rest/service areas in a country as a new seed file (Phase 2)
pixi run shaduw discover --country NL --out configs/seeds/nl-discovered.geojson
```

One stop = one unit of work; per-stop results are cached in `cache/` so
interrupted runs resume where they left off (`--force` recomputes). Outputs
land in `output/`.

### Configuration

One TOML file per run, see the documented example
[configs/nl-prototype.toml](configs/nl-prototype.toml): seed stops (GeoJSON
points or polygons), STAC settings, canopy source, shadow model date/hours,
score weights and output paths.

### Output schema (main table)

One row per stop: `stop_id`, `name`, `country`, `geometry` (parking polygon),
`centroid`, `ndvi_summer/winter/delta`, `tree_fraction`,
`mean_canopy_height`, `shadow_frac_detected_{djf,mam,jja,son}`,
`shadow_frac_modeled_{12h,15h}`, `shade_score`, `shade_class`,
`n_scenes_used`, `processed_at`.

## Known limitations

- 10 m Sentinel-2 pixels describe the *stop*, not an individual parking spot.
- Detected shadows are morning-only (~10:30); afternoons come from the model.
- Canopy layers have a vintage (Meta/WRI ~2018-2020 imagery, ETH 2020) —
  recently felled/planted trees are missed.
- Winter composites can be thin in NW Europe; check `n_scenes_used`.
- Buildings/carports also give shade but are not in canopy models (yet).

## Configuration

The quick-look Folium map (`outputs.map_html`) uses CartoDB basemap tiles.
They work fine unauthenticated, but you can raise the rate limit with a
[CARTO API key](https://carto.com/basemaps):

- Locally, copy `.env.example` to `.env` and set `CARTO_API_KEY` — it's
  loaded automatically and never committed (`.env` is gitignored).
- In GitHub Actions, set a `CARTO_API_KEY` repository secret; workflows that
  build the map pass it through as an environment variable.

## Development

```bash
pixi run test        # pytest (no network needed)
pixi run pre-commit  # lint
pixi run docs        # quarto docs preview
```

Developed by David Haasnoot, published under the GNU GPL-3 license.