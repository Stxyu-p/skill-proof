"""Model registry, downloader, and local ONNX embedder for Skill Proof.

Zero hard dependencies: stdlib-only for registry and download.
Optional dependencies: onnxruntime and tokenizers for local inference.
If optional dependencies or weights are absent, functions fail open gracefully.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import shutil
import time
import urllib.request
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

# Real, verified HuggingFace model configurations (verified live on 2026-10-11).
# Source URLs point directly to HuggingFace. Licenses and credits preserved.
MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "Xenova/multilingual-e5-small": {
        "repo_id": "Xenova/multilingual-e5-small",
        "name": "Multilingual E5 Small (Quantized ONNX)",
        "license": "MIT",
        "credit": "intfloat/multilingual-e5-small (MIT), quantized by Xenova (MIT)",
        "source_url": "https://huggingface.co/Xenova/multilingual-e5-small",
        "dim": 384,
        "prefix_query": "query: ",
        "prefix_passage": "passage: ",
        "onnx_relpath": "onnx/model_quantized.onnx",
        "files": {
            "config.json": {
                "size": 658,
                "sha256": "cb99455288675345e1a4f411438d5d0adbba5fbd3a67ea4fb03c015433b996c1",
            },
            "tokenizer_config.json": {
                "size": 443,
                "sha256": "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
            },
            "tokenizer.json": {
                "size": 17082730,
                "sha256": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
            },
            "onnx/model_quantized.onnx": {
                "size": 118308185,
                "sha256": "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
            },
        },
    },
    "Xenova/multilingual-e5-small-fp32": {
        "repo_id": "Xenova/multilingual-e5-small",
        "name": "Multilingual E5 Small (FP32 ONNX Fallback)",
        "license": "MIT",
        "credit": "intfloat/multilingual-e5-small (MIT), converted by Xenova (MIT)",
        "source_url": "https://huggingface.co/Xenova/multilingual-e5-small",
        "dim": 384,
        "prefix_query": "query: ",
        "prefix_passage": "passage: ",
        "onnx_relpath": "onnx/model.onnx",
        "files": {
            "config.json": {
                "size": 658,
                "sha256": "cb99455288675345e1a4f411438d5d0adbba5fbd3a67ea4fb03c015433b996c1",
            },
            "tokenizer_config.json": {
                "size": 443,
                "sha256": "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
            },
            "tokenizer.json": {
                "size": 17082730,
                "sha256": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
            },
            "onnx/model.onnx": {
                "size": 470268533,
                "sha256": "d41b5c754dc6e3df7e6289c74f6f845256a413d3ce67ddbdd542a9d3bf4170dc",
            },
        },
    },
    "Xenova/paraphrase-multilingual-MiniLM-L12-v2": {
        "repo_id": "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
        "name": "Paraphrase Multilingual MiniLM L12 v2 (Quantized ONNX)",
        "license": "Apache-2.0",
        "credit": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (Apache-2.0), quantized by Xenova",
        "source_url": "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2",
        "dim": 384,
        "prefix_query": "",
        "prefix_passage": "",
        "onnx_relpath": "onnx/model_quantized.onnx",
        "files": {
            "config.json": {
                "size": 673,
                "sha256": "05b570bff786faa5c4604152aa16f19f77ed6dfc31e47dd0f3dd987078693ac7",
            },
            "tokenizer_config.json": {
                "size": 496,
                "sha256": "3f5961b9ac86288cccdb97f32fb848d6187c78e1603958c53f3ea1f296b7d8a2",
            },
            "tokenizer.json": {
                "size": 17082913,
                "sha256": "b60b6b43406a48bf3638526314f3d232d97058bc93472ff2de930d43686fa441",
            },
            "onnx/model_quantized.onnx": {
                "size": 118308126,
                "sha256": "66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc",
            },
        },
    },
}

# Aliases for convenience
_ALIASES = {
    "default": "Xenova/multilingual-e5-small",
    "e5-small": "Xenova/multilingual-e5-small",
    "e5-small-fp32": "Xenova/multilingual-e5-small-fp32",
    "minilm": "Xenova/paraphrase-multilingual-MiniLM-L12-v2",
}


def get_model_spec(key_or_alias: str) -> Optional[dict[str, Any]]:
    clean = str(key_or_alias or "").strip()
    if clean in MODEL_REGISTRY:
        return MODEL_REGISTRY[clean]
    alias_target = _ALIASES.get(clean.lower())
    if alias_target and alias_target in MODEL_REGISTRY:
        return MODEL_REGISTRY[alias_target]
    for k, v in MODEL_REGISTRY.items():
        if k.lower() == clean.lower() or v["repo_id"].lower() == clean.lower():
            return v
    return None


def default_models_dir() -> pathlib.Path:
    env_dir = os.environ.get("HERMES_MODELS_DIR")
    if env_dir:
        return pathlib.Path(env_dir)
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        base = pathlib.Path(hermes_home)
    elif os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = pathlib.Path(local_app_data) / "hermes" if local_app_data else pathlib.Path.home() / ".hermes"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = pathlib.Path(xdg_data) / "hermes" if xdg_data else pathlib.Path.home() / ".local" / "share" / "hermes"
    global_models = base / "models"
    if global_models.exists():
        return global_models
    home_models = pathlib.Path.home() / ".hermes" / "models"
    if home_models.exists():
        return home_models
    legacy = base / "plugin-data" / "skill-proof" / "models"
    if legacy.exists():
        return legacy
    return global_models


def _sanitize_dirname(repo_id: str) -> str:
    return repo_id.replace("/", "_").replace("\\", "_")


def get_model_dir(model_key: str, root_dir: Optional[pathlib.Path | str] = None) -> pathlib.Path:
    base = pathlib.Path(root_dir) if root_dir else default_models_dir()
    spec = get_model_spec(model_key)
    repo_id = spec["repo_id"] if spec else model_key
    if spec and model_key == "Xenova/multilingual-e5-small-fp32":
        dir_name = f"{_sanitize_dirname(repo_id)}_fp32"
    else:
        dir_name = _sanitize_dirname(repo_id)
    return base / dir_name


def _file_sha256_matches(path: pathlib.Path, expected_sha256: str) -> bool:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest().lower() == expected_sha256.lower()
    except OSError:
        return False


def check_model_status(model_key: str, root_dir: Optional[pathlib.Path | str] = None) -> dict[str, Any]:
    spec = get_model_spec(model_key)
    if not spec:
        return {
            "status": "unknown_model",
            "installed": False,
            "valid": False,
            "missing_files": [],
            "size_bytes": 0,
            "spec": None,
        }

    target_dir = get_model_dir(model_key, root_dir)
    if not target_dir.is_dir():
        return {
            "status": "not_installed",
            "installed": False,
            "valid": False,
            "missing_files": list(spec["files"].keys()),
            "size_bytes": 0,
            "spec": spec,
        }

    missing: list[str] = []
    total_bytes = 0
    valid = True

    for relpath, meta in spec["files"].items():
        fp = target_dir / relpath
        if not fp.is_file():
            missing.append(relpath)
            valid = False
            continue
        actual_size = fp.stat().st_size
        total_bytes += actual_size
        if actual_size != meta["size"]:
            valid = False
            missing.append(f"{relpath} (size mismatch: {actual_size} != {meta['size']})")
        elif not _file_sha256_matches(fp, meta["sha256"]):
            valid = False
            missing.append(f"{relpath} (sha256 mismatch)")

    installed = len(missing) == 0 and valid
    return {
        "status": "installed" if installed else "incomplete",
        "installed": installed,
        "valid": valid,
        "missing_files": missing,
        "size_bytes": total_bytes,
        "spec": spec,
        "path": str(target_dir),
    }


def download_file_verified(
    url: str,
    target_path: pathlib.Path,
    expected_size: int,
    expected_sha256: str,
    timeout: float = 120.0,
    callback: Optional[Callable[[int, int], None]] = None,
) -> bool:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + f".tmp_{os.getpid()}")

    hasher = hashlib.sha256()
    received_bytes = 0
    req = urllib.request.Request(url, headers={"User-Agent": "skill-proof/0.15.0"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(temp_path, "wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    hasher.update(chunk)
                    received_bytes += len(chunk)
                    if callback:
                        callback(received_bytes, expected_size)

        if received_bytes != expected_size:
            logger.error("Download size mismatch for %s: got %d, expected %d", url, received_bytes, expected_size)
            if temp_path.exists():
                temp_path.unlink()
            return False

        actual_sha = hasher.hexdigest()
        if actual_sha.lower() != expected_sha256.lower():
            logger.error("Download SHA256 mismatch for %s: got %s, expected %s", url, actual_sha, expected_sha256)
            if temp_path.exists():
                temp_path.unlink()
            return False

        if target_path.exists():
            target_path.unlink()
        temp_path.rename(target_path)
        return True
    except Exception as exc:
        logger.error("Download error for %s: %s", url, exc)
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False


def download_model(
    model_key: str,
    root_dir: Optional[pathlib.Path | str] = None,
    callback: Optional[Callable[[str, int, int], None]] = None,
) -> bool:
    spec = get_model_spec(model_key)
    if not spec:
        logger.error("Cannot download unknown model: %s", model_key)
        return False

    target_dir = get_model_dir(model_key, root_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    repo_id = spec["repo_id"]

    for relpath, meta in spec["files"].items():
        fp = target_dir / relpath
        if fp.is_file() and fp.stat().st_size == meta["size"] and _file_sha256_matches(fp, meta["sha256"]):
            # Quick check if already downloaded and verified
            continue
        url = f"https://huggingface.co/{repo_id}/resolve/main/{relpath}"
        logger.info("Downloading %s -> %s...", url, fp)

        def _file_progress(curr: int, tot: int):
            if callback:
                callback(relpath, curr, tot)

        ok = download_file_verified(
            url,
            fp,
            expected_size=meta["size"],
            expected_sha256=meta["sha256"],
            callback=_file_progress,
        )
        if not ok:
            logger.error("Failed to download/verify file: %s", relpath)
            return False

    return True


def remove_model(model_key: str, root_dir: Optional[pathlib.Path | str] = None) -> bool:
    target_dir = get_model_dir(model_key, root_dir)
    if not target_dir.exists():
        return True
    try:
        shutil.rmtree(target_dir)
        return True
    except Exception as exc:
        logger.error("Failed to remove model directory %s: %s", target_dir, exc)
        return False


class LocalEmbedder:
    """CPU ONNX runtime embedding engine for local models.

    Imports onnxruntime and tokenizers lazily. Fails open if not installed.
    """

    def __init__(
        self,
        model_key: str = "Xenova/multilingual-e5-small",
        root_dir: Optional[pathlib.Path | str] = None,
    ) -> None:
        self.model_key = str(model_key)
        self.root_dir = root_dir
        self.spec = get_model_spec(model_key)
        self._session: Any = None
        self._tokenizer: Any = None
        self._loaded: bool = False
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        if not self.spec:
            self._available = False
            return False
        status = check_model_status(self.model_key, self.root_dir)
        if not status["installed"]:
            self._available = False
            return False
        try:
            import onnxruntime  # noqa: F401
            from tokenizers import Tokenizer  # noqa: F401
            self._available = True
            return True
        except ImportError:
            self._available = False
            return False

    def load(self) -> bool:
        if self._loaded and self._session is not None:
            return True
        if not self.is_available():
            return False

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            target_dir = get_model_dir(self.model_key, self.root_dir)
            tokenizer_path = target_dir / "tokenizer.json"
            onnx_path = target_dir / self.spec["onnx_relpath"]

            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._tokenizer.enable_truncation(max_length=512)
            self._tokenizer.enable_padding(pad_id=0, pad_token="<pad>")

            # CPUExecutionProvider is always available in onnxruntime
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = 2
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self._session = ort.InferenceSession(
                str(onnx_path),
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            )
            self._loaded = True
            return True
        except Exception as exc:
            logger.error("Failed to load LocalEmbedder for %s: %s", self.model_key, exc)
            self._available = False
            self._loaded = False
            return False

    def embed(self, texts: Sequence[str], is_query: bool = False) -> list[tuple[float, ...]]:
        if not texts or not self.load():
            return []

        prefix = self.spec.get("prefix_query" if is_query else "prefix_passage", "")
        formatted = [f"{prefix}{t}" for t in texts]

        try:
            import numpy as np

            encodings = self._tokenizer.encode_batch(formatted)
            input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

            # Check required model inputs
            model_inputs = {inp.name: None for inp in self._session.get_inputs()}
            feed_dict: dict[str, Any] = {}
            if "input_ids" in model_inputs:
                feed_dict["input_ids"] = input_ids
            if "attention_mask" in model_inputs:
                feed_dict["attention_mask"] = attention_mask
            if "token_type_ids" in model_inputs:
                feed_dict["token_type_ids"] = np.zeros_like(input_ids, dtype=np.int64)

            outputs = self._session.run(None, feed_dict)
            last_hidden_state = outputs[0]  # shape: (batch_size, seq_len, hidden_dim)

            # Mean pooling over attention mask
            input_mask_expanded = np.expand_dims(attention_mask, -1).astype(float)
            sum_embeddings = np.sum(last_hidden_state * input_mask_expanded, axis=1)
            sum_mask = np.clip(input_mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
            pooled = sum_embeddings / sum_mask

            # L2 normalization
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            norms = np.clip(norms, a_min=1e-9, a_max=None)
            normalized = pooled / norms

            return [tuple(float(v) for v in row) for row in normalized]
        except Exception as exc:
            logger.error("Inference error in LocalEmbedder: %s", exc)
            return []
