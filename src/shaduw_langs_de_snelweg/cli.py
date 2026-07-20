"""Command-line interface: ``shaduw run`` and ``shaduw discover``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shaduw",
        description="Shade detection at roadside stops from open satellite data.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the pipeline for a config file")
    p_run.add_argument("--config", required=True, type=Path, help="TOML config path")
    p_run.add_argument(
        "--force", action="store_true", help="ignore cached per-stop results"
    )
    p_run.add_argument("--limit", type=int, default=None, help="process first N stops")
    p_run.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel stop-processing threads (the work is I/O-bound)",
    )

    p_disc = sub.add_parser(
        "discover", help="discover rest/service areas in a country via Overpass"
    )
    p_disc.add_argument("--country", required=True, help="ISO 3166-1 alpha-2, e.g. NL")
    p_disc.add_argument("--out", required=True, type=Path, help="output seed GeoJSON")

    p_merge = sub.add_parser(
        "merge", help="merge result GeoParquets (e.g. per country) into one output set"
    )
    p_merge.add_argument("inputs", nargs="+", type=Path, help="result parquet files")
    p_merge.add_argument("--out", required=True, type=Path, help="output directory")
    p_merge.add_argument(
        "--roads-path",
        type=Path,
        default=Path("configs/roads/eu-major-roads.geojson"),
        help="static major-roads GeoJSON for the map overlay (see 'shaduw fetch-roads')",
    )

    p_roads = sub.add_parser(
        "fetch-roads",
        help="(re)build the static major-roads map overlay via Overpass",
    )
    p_roads.add_argument(
        "--countries", required=True, nargs="+", help="ISO 3166-1 alpha-2 codes, e.g. NL DE FR"
    )
    p_roads.add_argument(
        "--out",
        type=Path,
        default=Path("configs/roads/eu-major-roads.geojson"),
        help="output GeoJSON (loaded by 'run'/'merge' at map-render time)",
    )
    p_roads.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="per-country cache so a rebuild only re-fetches new countries",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        # rasterio logs a harmless session fallback (public buckets need no
        # credentials) and every transient GDAL read error at INFO level
        logging.getLogger("rasterio").setLevel(logging.WARNING)

    if args.command == "run":
        from shaduw_langs_de_snelweg.config import load_config
        from shaduw_langs_de_snelweg.pipeline import run_pipeline

        cfg = load_config(args.config)
        gdf = run_pipeline(
            cfg, force=args.force, limit=args.limit, workers=args.workers
        )
        print(
            gdf[
                ["stop_id", "name", "shade_score", "shade_class", "n_scenes_used"]
            ].to_string(index=False)
        )
        print(f"\n{len(gdf)} stops written to {cfg.output.directory}")
        return 0

    if args.command == "merge":
        from shaduw_langs_de_snelweg.config import OutputConfig
        from shaduw_langs_de_snelweg.outputs import merge_results, write_outputs

        gdf = merge_results(args.inputs)
        write_outputs(gdf, OutputConfig(directory=args.out), roads_path=args.roads_path)
        print(f"{len(gdf)} stops merged into {args.out}")
        return 0

    if args.command == "discover":
        from shaduw_langs_de_snelweg.stops import discover_stops

        gdf = discover_stops(args.country)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(args.out, driver="GeoJSON")
        print(f"{len(gdf)} stops discovered in {args.country} -> {args.out}")
        return 0

    if args.command == "fetch-roads":
        from shaduw_langs_de_snelweg.roads import build_major_roads_dataset

        gdf = build_major_roads_dataset(args.countries, args.cache_dir, args.out)
        print(f"{len(gdf)} major-road routes written to {args.out}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
