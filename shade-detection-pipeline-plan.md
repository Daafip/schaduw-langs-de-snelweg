# Plan: Shade Detection at Roadside Stops (EU) from Open Satellite Data

## 1. Goal

For a set of stopping places along EU highways (rest areas, service stations, parkings), estimate **how shaded each stop is**, including **seasonal variation**, using only openly available data. The pipeline produces a **tabular dataset with geometries** (GeoParquet + GeoPackage) that can drive a web map — and can later plug into the existing `speel-tuinen-langs-de-snelweg` project.

## 2. Core insight: don't rely on shadows alone

Sentinel-2 (the workhorse of free imagery, 10 m resolution) always acquires around **~10:30 local solar time**. Shadows visible in the imagery therefore only represent *morning* shade, and 10 m pixels are coarse relative to individual parking spots. So the plan combines **two complementary signals**:

1. **Direct signal** — shadow/darkness detected in Sentinel-2 imagery (spectral shadow index + Scene Classification Layer), compared across seasons.
2. **Indirect, more robust signal** — *what casts the shade*: vegetation presence and height. From a canopy height model + solar geometry you can **compute** the shadow footprint for any date/time (e.g., 15:00 on a summer afternoon — when shade actually matters for a parked car with a baby in it).

The seasonal comparison of NDVI (summer vs. winter) additionally tells you whether trees are **deciduous** — i.e., whether the shade exists in summer but not in winter, which is exactly the desirable configuration.

## 3. Open data sources

| Data | Source | Resolution | Use |
|---|---|---|---|
| Stop locations | OpenStreetMap via Overpass API (`highway=rest_area`, `highway=services`, `amenity=parking` near motorways) | vector | Location seeds + parking polygons |
| Optical imagery | Sentinel-2 L2A via STAC (Copernicus Data Space Ecosystem, or AWS `earth-search` — no account needed) | 10 m | NDVI, shadow index, seasonal composites |
| Canopy height | ETH Global Canopy Height 2020 (10 m) and/or Meta/WRI global canopy height (1 m, on AWS Open Data) | 1–10 m | Tree presence & height → shadow casting |
| Terrain | Copernicus DEM GLO-30 | 30 m | Terrain shading (usually minor, optional) |
| Sun position | `pvlib` / `astral` (computed, not downloaded) | — | Solar azimuth/elevation per date/time |
| Validation | National open orthophotos (e.g., PDOK Luchtfoto for NL, ~8 cm) | cm-scale | Manual ground truth for a sample |

Everything above is free and requires at most a free Copernicus account (avoidable by using the AWS STAC endpoint).

## 4. Pipeline design

Config-driven Python pipeline (Pixi environment), one stop = one unit of work, results cached on disk so runs are resumable.

### Step 1 — Location ingestion
- Input: a small seed list (start with 3–5 stops you know personally, mixed shady/unshaded) as a GeoJSON/CSV of points or OSM IDs.
- Fetch the actual **parking polygon** from OSM where available; otherwise buffer the point (~75 m).
- Define the **analysis AOI** = parking polygon + 30 m buffer (trees *next to* a parking cast shade *onto* it).

### Step 2 — Imagery acquisition (per stop, per season)
- STAC search on Sentinel-2 L2A: four seasonal windows (e.g., Dec–Feb, Mar–May, Jun–Aug, Sep–Nov) over the last 2 years, cloud cover < 30%.
- Load only the needed bands clipped to the AOI (`odc-stac`/`stackstac` + `rioxarray`) — a few hundred KB per scene, no full-tile downloads.
- Mask clouds/cloud-shadows using the SCL band; build a **median composite per season**.

### Step 3 — Per-stop metrics
Computed over the parking polygon (and separately over the buffer ring):

- `ndvi_summer`, `ndvi_winter`, `ndvi_delta` — vegetation and its deciduousness.
- `tree_fraction` — share of AOI with canopy height > 3 m.
- `mean_canopy_height` — within the buffer around the parking.
- `shadow_frac_detected_<season>` — fraction of dark pixels (shadow index / SCL) per seasonal composite, at ~10:30.
- `shadow_frac_modeled_12h`, `shadow_frac_modeled_15h` — **geometric shadow casting**: rasterized canopy height + sun position (summer solstice, 12:00 and 15:00 local) → shadow footprint intersected with the parking polygon. This is the headline metric.

### Step 4 — Shade score
Combine into one interpretable score 0–1, e.g. weighted:
`score = 0.5 * shadow_frac_modeled_15h + 0.3 * tree_fraction + 0.2 * shadow_frac_detected_summer`
Weights to be tuned against the manual validation sample. Also output a categorical label (`none` / `partial` / `good`).

### Step 5 — Outputs
- **GeoParquet** (analysis-friendly, DuckDB-queryable) and **GeoPackage** (QGIS-friendly), one row per stop.
- Optional per-stop raster artifacts (NDVI composite, modeled shadow mask) as COGs for debugging.
- A **Folium/leafmap HTML map** colored by shade score as a quick visual check; later this feeds the static site.

### Output schema (main table)

| Column | Type | Description |
|---|---|---|
| `stop_id` | str | OSM ID or generated ID |
| `name` | str | Stop name |
| `country` | str | ISO code |
| `geometry` | polygon | Parking polygon (EPSG:4326) |
| `centroid` | point | For map markers |
| `ndvi_summer` / `ndvi_winter` / `ndvi_delta` | float | Seasonal vegetation |
| `tree_fraction` | float | Canopy > 3 m share in AOI |
| `mean_canopy_height` | float | m |
| `shadow_frac_detected_{djf,mam,jja,son}` | float | Observed shadow per season |
| `shadow_frac_modeled_12h` / `_15h` | float | Modeled summer shadow |
| `shade_score` | float | 0–1 composite |
| `shade_class` | str | none / partial / good |
| `n_scenes_used` | int | QA |
| `processed_at` | datetime | QA |

## 5. Phased build-up

**Phase 0 — Prototype (notebook, 3–5 stops).** Hand-pick stops with known shade conditions. Get STAC search, clipping, NDVI, and canopy sampling working end to end. Validate that the metrics separate the shady from the bare stops.

**Phase 1 — Pipeline (script, ~50 stops, 1 country).** Refactor to a CLI (`pipeline run --config nl.toml`), add caching, retries, and the geometric shadow model. Produce the first real GeoParquet + map. Manually score ~20 stops from orthophotos/street imagery and tune the score weights.

**Phase 2 — Scale (per-country batches).** OSM-driven stop discovery per country, parallel processing (per-stop tasks are independent and small), resume-on-failure. Store intermediate composites so reruns are cheap.

**Phase 3 — Integration.** Join with the playground dataset (`speel-tuinen-langs-de-snelweg`) — "playground + shade" is a strong combined filter — and publish on the map site.

## 6. Risks & limitations (be honest in the output)

- **Resolution mismatch**: 10 m Sentinel-2 pixels vs. individual parking spots — the score describes the *stop*, not a specific spot. The 1 m Meta canopy layer mitigates this for the modeled shadow.
- **Acquisition time**: detected shadows are morning-only; the modeled shadow is the answer for afternoons, but it depends on canopy data vintage (2020) — recently felled or planted trees are missed.
- **Winter cloudiness** in NW Europe: DJF composites may be thin; report `n_scenes_used` and degrade gracefully.
- **Buildings/canopies (carports, roofs)** also give shade but aren't in canopy models; optionally add OSM building footprints with assumed heights later.

## 7. Suggested stack

`pixi` env with: `pystac-client`, `odc-stac`, `rioxarray`, `xarray`, `geopandas`, `shapely`, `pvlib`, `numpy`, `duckdb`, `folium`. Shadow casting: simple numpy ray-marching over the CHM raster (a few dozen lines), or `rvt-py` if preferred.
