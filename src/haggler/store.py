"""Blob storage with one interface for the local filesystem, memory (tests) and GCS.

Paths are POSIX-style keys relative to the store root, e.g. "latest/prices.json".
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel


class Store(Protocol):
    def write_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        """Write and return a reference (URI) to the blob."""
        ...

    def read_bytes(self, key: str) -> bytes | None: ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str) -> list[str]: ...


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def dumps(obj: Any, *, indent: int | None = 1) -> bytes:
    return json.dumps(
        _to_jsonable(obj), ensure_ascii=False, indent=indent, sort_keys=False
    ).encode()


def write_json(store: Store, key: str, obj: Any, *, indent: int | None = 1) -> str:
    return store.write_bytes(key, dumps(obj, indent=indent))


def read_json(store: Store, key: str) -> Any | None:
    data = store.read_bytes(key)
    if data is None:
        return None
    if key.endswith(".gz"):
        data = gzip.decompress(data)
    return json.loads(data)


def write_text_gz(store: Store, key: str, text: str) -> str:
    return store.write_bytes(key, gzip.compress(text.encode()), content_type="application/gzip")


def read_text_gz(store: Store, key: str) -> str | None:
    data = store.read_bytes(key)
    return gzip.decompress(data).decode() if data is not None else None


class MemoryStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def write_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        self.blobs[key] = data
        return f"memory://{key}"

    def read_bytes(self, key: str) -> bytes | None:
        return self.blobs.get(key)

    def exists(self, key: str) -> bool:
        return key in self.blobs

    def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self.blobs if k.startswith(prefix))


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError(f"key escapes store root: {key}")
        return path

    def write_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path.as_uri()

    def read_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def list(self, prefix: str) -> list[str]:
        base = self.root.resolve()
        if not base.exists():
            return []
        return sorted(
            p.relative_to(base).as_posix()
            for p in base.rglob("*")
            if p.is_file() and p.relative_to(base).as_posix().startswith(prefix)
        )


class GcsStore:
    def __init__(self, bucket: str, prefix: str = "") -> None:
        from google.cloud import storage  # imported lazily: not needed for local runs

        self._bucket = storage.Client().bucket(bucket)
        self.bucket_name = bucket
        self.prefix = prefix.strip("/")

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def write_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        blob = self._bucket.blob(self._key(key))
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self.bucket_name}/{self._key(key)}"

    def read_bytes(self, key: str) -> bytes | None:
        blob = self._bucket.blob(self._key(key))
        return blob.download_as_bytes() if blob.exists() else None

    def exists(self, key: str) -> bool:
        return self._bucket.blob(self._key(key)).exists()

    def list(self, prefix: str) -> list[str]:
        full = self._key(prefix)
        cut = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[cut:] for b in self._bucket.list_blobs(prefix=full))


def store_from_settings(settings: Any) -> Store:
    if settings.store == "gcs":
        if not settings.gcs_bucket:
            raise ValueError("HAGGLER_STORE=gcs requires HAGGLER_GCS_BUCKET")
        return GcsStore(settings.gcs_bucket)
    if settings.store == "memory":
        return MemoryStore()
    return LocalStore(settings.local_dir)
