"""haggler CLI: argument parsing and dispatch to the job, probe, sync, site-data and checks."""

from __future__ import annotations

import json
import os
import shutil
import sys
import types
from pathlib import Path

import pytest
import yaml

import haggler.fetch.client
import haggler.research.deps
from conftest import FixtureFetcher
from haggler import cli
from haggler.catalog import load_bundles
from haggler.config import Settings, llm_credentials_available
from haggler.publish import SITE_FILES
from haggler.store import LocalStore

REPO = Path(__file__).parents[2]
PUBLISH_FIXTURES = REPO / "tests" / "publish" / "fixtures"
REAL_LOAD_ENV_FILE = cli.load_env_file
# Variables the .env tests may set; each is cleared for the test and restored afterwards.
DOTENV_VARS = (
    "HAGGLER_TEST_DOTENV",
    "HAGGLER_STORE",
    "HAGGLER_EXTRACT_MODEL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch) -> None:
    """main() loads ./.env: a developer's own .env must not leak into these tests."""
    monkeypatch.setattr(cli, "load_env_file", lambda path=None: False)


@pytest.fixture
def real_dotenv(monkeypatch, tmp_path: Path) -> Path:
    """The real .env loading, in an empty working directory and a clean LLM/store environment."""
    for name in DOTENV_VARS:
        monkeypatch.setenv(name, "")  # records the original value, so teardown restores it
        monkeypatch.delenv(name)
    monkeypatch.setattr(cli, "load_env_file", REAL_LOAD_ENV_FILE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


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


def test_probe_mode_choices_follow_the_probe_modes() -> None:
    from haggler.probe import MODES

    probe = next(
        a.choices["probe"]
        for a in cli.build_parser()._actions
        if getattr(a, "choices", None) and "probe" in a.choices
    )
    mode = next(a for a in probe._actions if a.dest == "mode")
    assert tuple(mode.choices) == MODES
    assert "bundles" in MODES


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
    # None = the default profile of data/curated/profiles.yaml and its residence.
    assert (args.data_dir, args.out, args.residence, args.profile) == (
        Path("data"),
        Path("web/src/data"),
        None,
        None,
    )


def test_no_command_prints_help(capsys) -> None:
    assert cli.main([]) == 2
    assert "COMANDO" in capsys.readouterr().err


# --------------------------------------------------------------------------- .env


def test_main_loads_dotenv_from_the_working_directory(real_dotenv: Path, monkeypatch) -> None:
    (real_dotenv / ".env").write_text(
        "GOOGLE_API_KEY=from-dotenv\n"
        "HAGGLER_STORE=memory          # local | gcs | memory\n"
        "HAGGLER_TEST_DOTENV=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HAGGLER_TEST_DOTENV", "from-environment")
    assert cli.main([]) == 2
    assert os.environ["GOOGLE_API_KEY"] == "from-dotenv"
    assert Settings().store == "memory"
    assert llm_credentials_available()
    # override=False: what is already in the environment (CI, the container) wins.
    assert os.environ["HAGGLER_TEST_DOTENV"] == "from-environment"


def test_main_without_dotenv_leaves_the_environment_alone(real_dotenv: Path) -> None:
    assert REAL_LOAD_ENV_FILE() is False
    assert cli.main([]) == 2
    assert "GOOGLE_API_KEY" not in os.environ
    assert Settings().store == "local"


def _env_example(directory: Path, uncomment: tuple[str, ...] = ()) -> Path:
    """.env.example copied as .env, with the `# NAME=...` lines for `uncomment` enabled."""
    lines = []
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        candidate = line.removeprefix("# ")
        lines.append(candidate if candidate.split("=", 1)[0] in uncomment else line)
    path = directory / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_env_example_enables_no_llm_backend_until_one_is_chosen(real_dotenv: Path) -> None:
    assert REAL_LOAD_ENV_FILE(_env_example(real_dotenv))
    assert os.environ["HAGGLER_STORE"] == "local"  # the inline comment is not part of the value
    for name in ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_CLOUD_PROJECT"):
        assert name not in os.environ, name
    assert not llm_credentials_available()


def test_env_example_ai_studio_alternative_does_not_go_to_vertex(real_dotenv: Path) -> None:
    from google import genai

    REAL_LOAD_ENV_FILE(_env_example(real_dotenv, ("GOOGLE_API_KEY",)))
    assert os.environ["GOOGLE_API_KEY"] == "your-ai-studio-key"
    assert "GOOGLE_GENAI_USE_ENTERPRISE" not in os.environ
    assert "GOOGLE_CLOUD_PROJECT" not in os.environ
    assert llm_credentials_available()
    # With the Vertex lines active google-genai would drop the key and call Vertex AI.
    assert genai.Client().vertexai is False


def test_env_example_vertex_alternative(real_dotenv: Path) -> None:
    vertex = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")
    REAL_LOAD_ENV_FILE(_env_example(real_dotenv, vertex))
    assert [os.environ[name] for name in vertex] == ["1", "your-gcp-project", "global"]
    assert "GOOGLE_API_KEY" not in os.environ
    assert llm_credentials_available()


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
    bad.write_text("meli_plus_co_total CO http://www.mercadolibre.com.co/x\n")
    args = ["probe", "--targets", str(bad), "--out", str(tmp_path / "o"), "--mode", "bundles"]
    assert cli.main(args) == 2
    assert "línea 1: la URL debe ser https://" in capsys.readouterr().err


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


def test_validate_catalog_checks_anchor_values_offline(tmp_path: Path, capsys) -> None:
    """An anchor must state the YAML value on its text alone: a Meli+ Total price anchor that
    quotes the 2-month intro price fails, one that quotes the YAML price passes."""
    catalog_dir, curated_dir = _copy_catalog(tmp_path)
    path = curated_dir / "bundles.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    (total,) = [b for b in data["bundles"] if b["bundle_id"] == "meli_plus_co_total"]
    amount = total["price"]["amount"]
    page = total["watch"]["pages"][0]["url"]
    argv = [
        "validate-catalog",
        "--catalog-dir",
        str(catalog_dir),
        "--curated-dir",
        str(curated_dir),
    ]

    def with_anchor(text: str) -> None:
        total["watch"]["anchors"] = [{"fact": "price", "url": page, "text": text}]
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with_anchor("Meli+ Total: $ 19.900/mes por 2 meses")
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert f"ERROR bundle meli_plus_co_total: price anchor does not state {amount} COP/month" in out

    with_anchor(f"Meli+ Total: $ {int(amount):,}/mes".replace(",", ".") + ", envío gratis")
    assert cli.main(argv) == 0, capsys.readouterr().out


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


def test_research_without_action_prints_usage(capsys) -> None:
    assert cli.main(["research"]) == 2
    assert "haggler research" in capsys.readouterr().err
    assert cli.main(["research", "nope"]) == 2


@pytest.fixture
def offline_fetcher(monkeypatch) -> FixtureFetcher:
    """`haggler research run` always runs the bundle watcher: these tests never reach a vendor
    site, whatever watch.mode the real bundles.yaml has (a fetcher without routes: 404)."""
    fetcher = FixtureFetcher()
    monkeypatch.setattr(haggler.fetch.client, "HttpFetcher", lambda settings: fetcher)
    return fetcher


def test_research_run_plan_only(capsys, monkeypatch, offline_fetcher) -> None:
    monkeypatch.setenv("HAGGLER_LLM", "off")
    assert cli.main(["research", "run", "--store", "memory", "--no-llm", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] and out["dry_run"] and not out["persisted"]
    assert out["planned"] and out["by_status"] == {"skipped": len(out["planned"])}


def test_research_run_watch_only(capsys, monkeypatch, offline_fetcher) -> None:
    monkeypatch.setenv("HAGGLER_LLM", "off")
    # One real bundle switched to auto: the watcher reads its pages, through the test's fetcher.
    real = load_bundles()
    auto = real.model_copy(
        update={
            "bundles": [
                b.model_copy(update={"watch": b.watch.model_copy(update={"mode": "auto"})})
                if b.bundle_id == "telia_se_obegransad_streaming"
                else b
                for b in real.bundles
            ]
        }
    )
    monkeypatch.setattr(haggler.research.deps, "load_bundles", lambda: auto)
    argv = ["research", "run", "--store", "memory", "--watch-only", "--dry-run"]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # The watcher alone: nothing planned for the agents, no search query.
    assert out["ok"] and out["planned"] == [] and out["budget"]["queries"] == 0
    assert offline_fetcher.calls and all("telia.se" in u for u in offline_fetcher.calls)
