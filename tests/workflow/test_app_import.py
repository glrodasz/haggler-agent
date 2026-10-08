"""`adk web agents` / `adk run` import the agent package at startup: it must work offline and
without credentials, even when the environment asks for the GCS store."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import socket

def no_network(*args, **kwargs):
    raise RuntimeError("network access during import")

socket.socket.connect = no_network
socket.create_connection = no_network

import haggler_agent
from google.adk.apps import App

assert isinstance(haggler_agent.app, App), type(haggler_agent.app)
assert haggler_agent.app.name == "haggler_agent"
assert haggler_agent.root_agent.name == "daily_prices"
print("ok", haggler_agent.root_agent.name)
"""


def test_import_haggler_agent_offline() -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE"}
    }
    env.update(
        {
            "PYTHONPATH": str(ROOT / "agents"),
            "HAGGLER_STORE": "gcs",  # GcsStore would need credentials: must stay lazy
            "HAGGLER_GCS_BUCKET": "",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().endswith("ok daily_prices")
