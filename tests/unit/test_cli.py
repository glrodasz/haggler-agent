"""haggler CLI: argument parsing and dispatch to the job, probe, sync, site-data and checks."""

from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from haggler import cli
from haggler.publish import SITE_FILES
from haggler.store import LocalStore

REPO = Path(__file__).parents[2]
PUBLISH_FIXTURES = REPO / "tests" / "publish" / "fixtures"


# --------------------------------------------------------------------------- parsing


def test_probe_defaults_and_options() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["probe"])
    assert (args.targets, args.out, args.mode) == (
        Path("probe/targets.txt"),
        Path("probe-out"),
        "adapters",
    )
    args = parser.parse_args(["probe", "--targets", "t.txt", "--out", "o", "--mode", "fx"])
    assert (args.targets, args.out, args.mode) == (Path("t.txt"), Path("o"), "fx")
    with pytest.raises(SystemExit):
        parser.parse_args(["probe", "--mode", "everything"])


def test_sync_source_is_required_and_exclusive() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["sync", "--from", "gs://bucket/some/prefix/"])
    assert args.from_gcs == ("bucket", "some/prefix")
    assert args.from_dir is None
    assert args.data_dir == Path("data")
    assert args.allow_older is False
    args = parser.parse_args(["sync", "--from-dir", "store", "--data-dir", "d", "--allow-older"])
    assert (args.from_gcs, args.from_dir, args.data_dir, args.allow_older) == (
        None,
        Path("store"),
        Path("d"),
        True,
    )
    for bad in (
        ["sync"],
        ["sync", "--from", "gs://b", "--from-dir", "x"],
        ["sync", "--from", "s3://bucket"],
        ["sync", "--from", "gs://"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(bad)


def test_gcs_location() -> None:
    assert cli._gcs_location("gs://my-bucket") == ("my-bucket", "")
    assert cli._gcs_location("gs://my-bucket/") == ("my-bucket", "")
    assert cli._gcs_location("gs://my-bucket/a/b") == ("my-bucket", "a/b")


def test_site_data_defaults() -> None:
    args = cli.build_parser().parse_args(["site-data"])
    assert (args.data_dir, args.out, args.residence, args.profile) == (
        Path("data"),
        Path("web/src/data"),
        "SE",
        "se_resident_co_citizen",
    )


def test_no_command_prints_help(capsys) -> None:
    assert cli.main([]) == 2
    assert "COMANDO" in capsys.readouterr().err


# --------------------------------------------------------------------------- daily


def test_daily_delegates_remaining_argv(monkeypatch) -> None:
    received: list[list[str]] = []

    def main(argv):
        received.append(list(argv))
        return 3

    module = types.ModuleType("haggler.jobs.daily")
    module.main = main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "haggler.jobs.daily", module)
    argv = ["daily", "--store", "memory", "--services", "netflix,spotify", "--dry-run", "--help"]
    assert cli.main(argv) == 3
    assert received == [argv[1:]]


def test_daily_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.setitem(sys.modules, "haggler.jobs.daily", None)  # import -> ImportError
    assert cli.main(["daily", "--dry-run"]) == 1
    assert "haggler.jobs.daily" in capsys.readouterr().err


# --------------------------------------------------------------------------- probe


def test_probe_dispatch(tmp_path: Path, monkeypatch) -> None:
    import haggler.probe

    calls: list[tuple] = []
    monkeypatch.setattr(haggler.probe, "run_probe", lambda *a, **k: calls.append(a) or 0)
    targets = tmp_path / "targets.txt"
    targets.write_text("netflix SE\n")
    assert cli.main(["probe", "--targets", str(targets), "--out", str(tmp_path / "o")]) == 0
    assert calls == [(targets, tmp_path / "o", "adapters")]


def test_probe_missing_targets_or_bad_targets(tmp_path: Path, capsys) -> None:
    assert cli.main(["probe", "--targets", str(tmp_path / "nope.txt")]) == 2
    assert "no existe" in capsys.readouterr().err
    bad = tmp_path / "bad.txt"
    bad.write_text("not_a_service SE\n")
    assert cli.main(["probe", "--targets", str(bad), "--out", str(tmp_path / "o")]) == 2
    assert "servicio desconocido" in capsys.readouterr().err


# --------------------------------------------------------------------------- sync


def test_sync_from_dir(tmp_path: Path, capsys) -> None:
    data = tmp_path / "data"
    code = cli.main(["sync", "--from-dir", str(PUBLISH_FIXTURES / "data"), "--data-dir", str(data)])
    assert code == 0
    out = capsys.readouterr().out
    assert "20261008T041700Z-ab12cd" in out
    assert "23 precios" in out
    assert "23 nuevos" in out
    assert (data / "latest" / "run.json").exists()
    assert (data / "changes" / "2026-10.jsonl").exists()


def test_sync_errors(tmp_path: Path, capsys) -> None:
    assert cli.main(["sync", "--from-dir", str(tmp_path / "missing")]) == 2
    assert "no existe el directorio" in capsys.readouterr().err

    source = tmp_path / "src"
    shutil.copytree(PUBLISH_FIXTURES / "data" / "latest", source / "latest")
    (source / "latest" / "fx.json").write_text("{}\n")
    code = cli.main(["sync", "--from-dir", str(source), "--data-dir", str(tmp_path / "data")])
    assert code == 1
    err = capsys.readouterr().err
    assert "fx.json" in err and "no coincide" in err
    assert not (tmp_path / "data").exists()


def test_sync_from_gcs(tmp_path: Path, monkeypatch, capsys) -> None:
    import haggler.store

    created: list[tuple[str, str]] = []

    class FakeGcs(LocalStore):
        def __init__(self, bucket: str, prefix: str = "") -> None:
            created.append((bucket, prefix))
            super().__init__(PUBLISH_FIXTURES / "data")

    monkeypatch.setattr(haggler.store, "GcsStore", FakeGcs)
    data = tmp_path / "data"
    assert cli.main(["sync", "--from", "gs://proj-haggler-data/prod", "--data-dir", str(data)]) == 0
    assert created == [("proj-haggler-data", "prod")]
    assert "sync gs://proj-haggler-data/prod ->" in capsys.readouterr().out


# --------------------------------------------------------------------------- site-data


def test_site_data_on_empty_data(tmp_path: Path, capsys) -> None:
    out = tmp_path / "site"
    code = cli.main(["site-data", "--data-dir", str(tmp_path / "data"), "--out", str(out)])
    assert code == 0
    assert sorted(p.stem for p in out.glob("*.json")) == sorted(SITE_FILES)
    assert json.loads((out / "meta.json").read_text())["run_id"] is None
    assert "10 archivos" in capsys.readouterr().out


def test_site_data_with_fixture_catalog(tmp_path: Path) -> None:
    out = tmp_path / "site"
    code = cli.main(
        [
            "site-data",
            "--data-dir",
            str(PUBLISH_FIXTURES / "data"),
            "--out",
            str(out),
            "--catalog-dir",
            str(PUBLISH_FIXTURES / "catalog"),
            "--curated-dir",
            str(PUBLISH_FIXTURES / "curated"),
        ]
    )
    assert code == 0
    assert len(json.loads((out / "offers.json").read_text())) == 21


def test_site_data_bad_residence(tmp_path: Path, capsys) -> None:
    code = cli.main(["site-data", "--data-dir", str(tmp_path), "--out", str(tmp_path / "o"),
                     "--residence", "XX"])  # fmt: skip
    assert code == 1
    assert "residencia" in capsys.readouterr().err


# --------------------------------------------------------------------------- validate-catalog


def test_validate_catalog_ok(capsys) -> None:
    assert cli.main(["validate-catalog"]) == 0
    assert "catálogo OK" in capsys.readouterr().out


def _copy_catalog(tmp_path: Path) -> tuple[Path, Path]:
    catalog_dir, curated_dir = tmp_path / "catalog", tmp_path / "curated"
    shutil.copytree(REPO / "catalog", catalog_dir)
    shutil.copytree(REPO / "data" / "curated", curated_dir)
    return catalog_dir, curated_dir


def test_validate_catalog_integrity_errors(tmp_path: Path, capsys) -> None:
    catalog_dir, curated_dir = _copy_catalog(tmp_path)
    services = catalog_dir / "services.yaml"
    services.write_text(services.read_text().replace("adapter: spotify_premium", "adapter: nope"))
    argv = [
        "validate-catalog",
        "--catalog-dir",
        str(catalog_dir),
        "--curated-dir",
        str(curated_dir),
    ]
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert "ERROR spotify: unknown adapter nope" in captured.out
    assert "1 errores de integridad" in captured.err


def test_validate_catalog_schema_errors(tmp_path: Path, capsys) -> None:
    catalog_dir, curated_dir = _copy_catalog(tmp_path)
    (curated_dir / "bundles.yaml").write_text("bundles:\n  - bundle_id: x\n")
    argv = [
        "validate-catalog",
        "--catalog-dir",
        str(catalog_dir),
        "--curated-dir",
        str(curated_dir),
    ]
    assert cli.main(argv) == 1
    assert "no se pudo cargar" in capsys.readouterr().err

    (curated_dir / "bundles.yaml").write_text("bundles: [unclosed\n")
    assert cli.main(argv) == 1
    assert "no se pudo cargar" in capsys.readouterr().err
