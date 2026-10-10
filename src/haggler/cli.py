"""`haggler` command line (console script).

  haggler daily [...]            daily job; arguments are parsed by haggler.jobs.daily.main
  haggler research run [...]     weekly feasibility research and bundle watcher
                                 (haggler.jobs.research.main; --watch-only: the watcher alone)
  haggler research review|accept|reject [...]   review research findings (haggler.research.cli)
  haggler probe --targets F --out D [--mode adapters|fx|full|research|bundles]
  haggler sync (--from gs://BUCKET[/prefix] | --from-dir PATH) [--data-dir data]
  haggler site-data [--data-dir data] [--out web/src/data] [--residence SE] [--profile P]
  haggler validate-catalog

Heavy modules (the ADK pipeline, google-cloud-storage, httpx clients) are imported inside the
commands, so `validate-catalog` or `site-data` work even while the pipeline is broken or absent.

Settings come from environment variables (haggler.config); a `.env` file in the working directory
is loaded first, without overriding variables that are already set (see .env.example).
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

DEFAULT_RESIDENCE = "SE"
DEFAULT_PROFILE = "se_resident_co_citizen"


def _err(message: str) -> None:
    print(f"haggler: {message}", file=sys.stderr)


def _gcs_location(value: str) -> tuple[str, str]:
    """gs://bucket[/prefix] -> (bucket, prefix)."""
    if not value.startswith("gs://"):
        raise argparse.ArgumentTypeError(f"se espera gs://BUCKET[/prefijo], no {value!r}")
    bucket, _, prefix = value.removeprefix("gs://").partition("/")
    if not bucket:
        raise argparse.ArgumentTypeError(f"falta el bucket en {value!r}")
    return bucket, prefix.strip("/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="haggler",
        description="Precios de suscripciones por país: job diario, probe, sync y datos del sitio.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMANDO")

    # Listed for --help only: main() hands "daily" and everything after it to jobs.daily.main.
    sub.add_parser(
        "daily",
        add_help=False,
        help="corre el job diario (opciones: haggler daily --help)",
    ).add_argument("rest", nargs=argparse.REMAINDER)

    sub.add_parser(
        "research",
        add_help=False,
        help="investigación de factibilidad: run | review | accept | reject",
    ).add_argument("rest", nargs=argparse.REMAINDER)

    probe = sub.add_parser(
        "probe", help="descarga páginas reales y reporta qué extrae cada adapter"
    )
    probe.add_argument("--targets", type=Path, default=Path("probe/targets.txt"))
    probe.add_argument("--out", type=Path, default=Path("probe-out"))
    # Kept in step with haggler.probe.MODES (not imported: probe pulls in the HTML parser).
    probe.add_argument(
        "--mode", choices=("adapters", "fx", "full", "research", "bundles"), default="adapters"
    )

    sync = sub.add_parser("sync", help="copia la última corrida del store a data/ (con changelog)")
    source = sync.add_mutually_exclusive_group(required=True)
    source.add_argument("--from", dest="from_gcs", type=_gcs_location, metavar="gs://BUCKET[/P]")
    source.add_argument("--from-dir", type=Path, metavar="PATH")
    sync.add_argument("--data-dir", type=Path, default=Path("data"))
    sync.add_argument(
        "--allow-older",
        action="store_true",
        help="permite publicar una corrida más antigua que la de data/latest",
    )

    site = sub.add_parser("site-data", help="genera web/src/data/*.json desde data/")
    site.add_argument("--data-dir", type=Path, default=Path("data"))
    site.add_argument("--out", type=Path, default=Path("web/src/data"))
    # Defaults: the `default` profile of data/curated/profiles.yaml and its residence (without
    # that file: DEFAULT_PROFILE in DEFAULT_RESIDENCE).
    site.add_argument("--residence", default=None)
    site.add_argument("--profile", default=None)
    site.add_argument("--catalog-dir", type=Path, default=None, help=argparse.SUPPRESS)
    site.add_argument("--curated-dir", type=Path, default=None, help=argparse.SUPPRESS)

    check = sub.add_parser("validate-catalog", help="valida catalog/*.yaml y data/curated/*.yaml")
    check.add_argument("--catalog-dir", type=Path, default=None)
    check.add_argument("--curated-dir", type=Path, default=None)
    return parser


# ============================================================================ commands


def cmd_daily(argv: list[str]) -> int:
    try:
        daily = importlib.import_module("haggler.jobs.daily")
    except ImportError as exc:
        _err(f"el job diario no está disponible (haggler.jobs.daily): {exc}")
        return 1
    return int(daily.main(argv) or 0)


RESEARCH_USAGE = "uso: haggler research {run|review|accept|reject} [opciones]"


def cmd_research(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(RESEARCH_USAGE, file=sys.stderr)
        return 0 if argv else 2
    action, rest = argv[0], argv[1:]
    if action == "run":
        try:
            job = importlib.import_module("haggler.jobs.research")
        except ImportError as exc:
            _err(f"el job de investigación no está disponible: {exc}")
            return 1
        return int(job.main(rest) or 0)
    if action in ("review", "accept", "reject"):
        review_cli = importlib.import_module("haggler.research.cli")
        return int(review_cli.main(argv) or 0)
    _err(f"acción desconocida {action!r}; {RESEARCH_USAGE}")
    return 2


def cmd_probe(args: argparse.Namespace) -> int:
    from haggler.probe import run_probe

    if not args.targets.exists():
        _err(f"no existe el archivo de targets {args.targets}")
        return 2
    try:
        return run_probe(args.targets, args.out, args.mode)
    except ValueError as exc:
        _err(str(exc))
        return 2


def cmd_sync(args: argparse.Namespace) -> int:
    from haggler.publish import SyncError, sync
    from haggler.store import GcsStore, LocalStore

    if args.from_gcs:
        bucket, prefix = args.from_gcs
        source = GcsStore(bucket, prefix)
        label = f"gs://{bucket}/{prefix}".rstrip("/")
    else:
        if not args.from_dir.is_dir():
            _err(f"no existe el directorio {args.from_dir}")
            return 2
        source = LocalStore(args.from_dir)
        label = str(args.from_dir)
    try:
        result = sync(
            source, args.data_dir, today=datetime.now(UTC).date(), allow_older=args.allow_older
        )
    except SyncError as exc:
        _err(f"sync desde {label} falló: {exc}")
        return 1
    counts = result.counts()
    print(
        f"sync {label} -> {args.data_dir}: corrida {result.run_id} ({result.as_of}), "
        f"{result.prices} precios; cambios: {counts['added']} nuevos, "
        f"{counts['changed']} modificados, {counts['removed']} retirados "
        f"({result.appended} agregados al changelog)"
    )
    if result.research_run_id:
        print(
            f"investigación {result.research_run_id}: {result.research_subjects} sujetos en "
            f"{args.data_dir}/research"
        )
    for warning in result.warnings:
        print(f"aviso: {warning}", file=sys.stderr)
    return 0


def cmd_site_data(args: argparse.Namespace) -> int:
    from haggler.catalog import (
        load_bundles,
        load_catalog,
        load_feasibility,
        load_payment_instruments,
        load_profiles,
        load_review,
    )
    from haggler.publish import site_data

    try:
        catalog = load_catalog(args.catalog_dir)
        feasibility = load_feasibility(args.curated_dir)
        bundles = load_bundles(args.curated_dir)
        profiles = load_profiles(args.curated_dir)
        instruments = load_payment_instruments(args.curated_dir, missing_ok=True)
        review = load_review(args.curated_dir)
        profile = args.profile or (profiles.default if profiles else DEFAULT_PROFILE)
        chosen = profiles.get(profile) if profiles else None
        residence = args.residence or (chosen.residence_country if chosen else DEFAULT_RESIDENCE)
        written = site_data(
            args.data_dir,
            args.out,
            residence=residence,
            profile=profile,
            catalog=catalog,
            feasibility=feasibility,
            bundles=bundles,
            profiles=profiles,
            instruments=instruments,
            review=review,
        )
    except (ValueError, OSError, KeyError) as exc:  # pydantic ValidationError is a ValueError
        _err(f"site-data falló: {exc}")
        return 1
    print(f"site-data: {len(written)} archivos en {args.out}")
    return 0


def cmd_validate_catalog(args: argparse.Namespace) -> int:
    import yaml

    from haggler.catalog import (
        integrity_errors,
        load_bundles,
        load_catalog,
        load_feasibility,
        load_payment_instruments,
        load_profiles,
        load_review,
    )

    try:
        catalog = load_catalog(args.catalog_dir)
        feasibility = load_feasibility(args.curated_dir)
        bundles = load_bundles(args.curated_dir)
        instruments = load_payment_instruments(args.curated_dir)
        profiles = load_profiles(args.curated_dir)
        review = load_review(args.curated_dir)
    except (ValidationError, ValueError, OSError, KeyError, yaml.YAMLError) as exc:
        _err(f"no se pudo cargar el catálogo: {type(exc).__name__}: {exc}")
        return 1
    errors = integrity_errors(catalog, feasibility, bundles, instruments, profiles, review)
    for error in errors:
        print(f"ERROR {error}")
    if errors:
        _err(f"{len(errors)} errores de integridad")
        return 1
    print(
        f"catálogo OK: {len(catalog.services)} servicios, {len(catalog.countries)} países, "
        f"{len(feasibility.records)} registros de factibilidad, {len(bundles.bundles)} bundles, "
        f"{len(instruments.instruments)} medios de pago, "
        f"{len(profiles.profiles) if profiles else 0} perfiles, "
        f"{len(review.decisions)} decisiones de revisión"
    )
    return 0


def load_env_file(path: Path | None = None) -> bool:
    """Load `path` (default: ./.env in the working directory) into os.environ.

    Variables already in the environment win (override=False): CI, the container (its image never
    contains a .env, see .dockerignore) and `VAR=x haggler ...` keep precedence. `uv run` does not
    read .env by itself. Returns whether a file was loaded.
    """
    path = Path(".env") if path is None else path
    if not path.is_file():
        return False
    load_dotenv(path, override=False)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    args_list = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=os.getenv("HAGGLER_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args_list and args_list[0] == "daily":
        return cmd_daily(args_list[1:])
    if args_list and args_list[0] == "research":
        return cmd_research(args_list[1:])
    parser = build_parser()
    args = parser.parse_args(args_list)
    commands = {
        "probe": cmd_probe,
        "sync": cmd_sync,
        "site-data": cmd_site_data,
        "validate-catalog": cmd_validate_catalog,
    }
    if args.command not in commands:
        parser.print_help(sys.stderr)
        return 2
    return commands[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
