"""Tests for the local LLM model manager (local_llm.py)."""

import os
import tempfile
from pathlib import Path

import pytest

from kiassist_utils.local_llm import (
    DownloadProgress,
    LocalModelManager,
    _human_readable_size,
)


@pytest.fixture
def tmp_models_dir(tmp_path):
    """Create a temporary models directory."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    return models_dir


@pytest.fixture
def manager(tmp_models_dir):
    """Create a LocalModelManager with a temporary directory."""
    return LocalModelManager(
        models_dir=tmp_models_dir,
        server_port=19999,
    )


# ---------------------------------------------------------------------------
# DownloadProgress
# ---------------------------------------------------------------------------


class TestDownloadProgress:
    def test_percent_zero_when_no_total(self):
        p = DownloadProgress(total_bytes=0, downloaded_bytes=100)
        assert p.percent == 0.0

    def test_percent_calculation(self):
        p = DownloadProgress(total_bytes=1000, downloaded_bytes=500)
        assert p.percent == 50.0

    def test_percent_caps_at_100(self):
        p = DownloadProgress(total_bytes=100, downloaded_bytes=200)
        assert p.percent == 100.0

    def test_to_dict_includes_percent(self):
        p = DownloadProgress(
            model_id="test",
            total_bytes=1000,
            downloaded_bytes=250,
            status="downloading",
        )
        d = p.to_dict()
        assert d["model_id"] == "test"
        assert d["percent"] == 25.0
        assert d["status"] == "downloading"


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


class TestGetAvailableModels:
    def test_returns_known_variants(self, manager: LocalModelManager):
        models = manager.get_available_models()
        ids = {m["id"] for m in models}
        assert "gemma4-e2b-q4_k_m" in ids
        assert "gemma4-e4b-q4_k_m" in ids
        assert "gemma4-26b-a4b-q4_k_m" in ids
        assert "gemma4-31b-q4_k_m" in ids

    def test_downloaded_flag_false_when_missing(self, manager: LocalModelManager):
        models = manager.get_available_models()
        for m in models:
            assert m["downloaded"] is False

    def test_downloaded_flag_true_when_file_exists(
        self, manager: LocalModelManager, tmp_models_dir: Path
    ):
        # Create a fake GGUF file for the E2B variant
        (tmp_models_dir / "google_gemma-4-E2B-it-Q4_K_M.gguf").write_bytes(b"fake")
        models = manager.get_available_models()
        m_e2b = next(m for m in models if m["id"] == "gemma4-e2b-q4_k_m")
        assert m_e2b["downloaded"] is True

    def test_discovers_extra_gguf_files(
        self, manager: LocalModelManager, tmp_models_dir: Path
    ):
        # Drop an unknown GGUF file
        (tmp_models_dir / "custom-model.gguf").write_bytes(b"x" * 100)
        models = manager.get_available_models()
        extra = [m for m in models if m["id"] == "custom-model"]
        assert len(extra) == 1
        assert extra[0]["downloaded"] is True

    def test_get_downloaded_models_filters(
        self, manager: LocalModelManager, tmp_models_dir: Path
    ):
        (tmp_models_dir / "google_gemma-4-E4B-it-Q4_K_M.gguf").write_bytes(b"fake")
        downloaded = manager.get_downloaded_models()
        assert len(downloaded) == 1
        assert downloaded[0]["id"] == "gemma4-e4b-q4_k_m"


# ---------------------------------------------------------------------------
# Delete model
# ---------------------------------------------------------------------------


class TestDeleteModel:
    def test_delete_unknown_model(self, manager: LocalModelManager):
        result = manager.delete_model("nonexistent")
        assert result["success"] is False

    def test_delete_missing_file(self, manager: LocalModelManager):
        result = manager.delete_model("gemma4-e2b-q4_k_m")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_delete_success(
        self, manager: LocalModelManager, tmp_models_dir: Path
    ):
        f = tmp_models_dir / "google_gemma-4-E2B-it-Q4_K_M.gguf"
        f.write_bytes(b"fake")
        result = manager.delete_model("gemma4-e2b-q4_k_m")
        assert result["success"] is True
        assert not f.exists()


# ---------------------------------------------------------------------------
# Server lifecycle (mocked)
# ---------------------------------------------------------------------------


class TestServerLifecycle:
    def test_start_unknown_model(self, manager: LocalModelManager):
        result = manager.start_server("nonexistent")
        assert result["success"] is False

    def test_start_model_not_downloaded(self, manager: LocalModelManager):
        result = manager.start_server("gemma4-e4b-q4_k_m")
        assert result["success"] is False
        assert "not downloaded" in result["error"].lower()

    def test_get_server_status_not_running(self, manager: LocalModelManager):
        status = manager.get_server_status()
        assert status["running"] is False
        assert status["url"] is None
        assert status["model_id"] is None

    def test_stop_when_not_running(self, manager: LocalModelManager):
        result = manager.stop_server()
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Download URL resolution (Hugging Face)
# ---------------------------------------------------------------------------


class TestResolveDownloadUrl:
    def test_resolves_hf_url(self, manager):
        variant = {
            "id": "gemma4-e2b-q4_k_m",
            "filename": "google_gemma-4-E2B-it-Q4_K_M.gguf",
            "hf_repo": "bartowski/google_gemma-4-E2B-it-GGUF",
        }
        url = manager._resolve_download_url(variant)
        assert url == (
            "https://huggingface.co/bartowski/google_gemma-4-E2B-it-GGUF"
            "/resolve/main/google_gemma-4-E2B-it-Q4_K_M.gguf"
        )

    def test_returns_none_when_no_hf_repo(self, manager):
        variant = {
            "id": "custom",
            "filename": "custom.gguf",
            "hf_repo": "",
        }
        url = manager._resolve_download_url(variant)
        assert url is None

    def test_all_known_variants_have_hf_url(self, manager):
        for model in manager.get_available_models():
            if model.get("hf_repo"):
                url = manager._resolve_download_url(model)
                assert url is not None
                assert "huggingface.co" in url
                assert model["filename"] in url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHumanReadableSize:
    def test_bytes(self):
        assert _human_readable_size(500) == "500.0 B"

    def test_megabytes(self):
        result = _human_readable_size(5 * 1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _human_readable_size(2 * 1024 * 1024 * 1024)
        assert "GB" in result


# ---------------------------------------------------------------------------
# Download model (start flow, not full network test)
# ---------------------------------------------------------------------------


class TestDownloadModel:
    def test_unknown_model_returns_error(self, manager):
        result = manager.download_model("no-such-model")
        assert result["success"] is False

    def test_already_downloaded(self, manager, tmp_models_dir):
        (tmp_models_dir / "google_gemma-4-E2B-it-Q4_K_M.gguf").write_bytes(b"fake")
        result = manager.download_model("gemma4-e2b-q4_k_m")
        assert result["success"] is True
        assert "already" in result.get("message", "").lower()

    def test_cancel_download(self, manager):
        result = manager.cancel_download()
        assert result["success"] is True
