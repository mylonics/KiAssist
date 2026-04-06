"""Tests for the local LLM model manager (local_llm.py)."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kiassist_utils.local_llm import (
    DownloadProgress,
    LocalModelManager,
    _human_readable_size,
    _DEFAULT_OLLAMA_PORT,
    _DEFAULT_OLLAMA_URL,
    _KNOWN_MODEL_VARIANTS,
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


# ---------------------------------------------------------------------------
# Ollama integration
# ---------------------------------------------------------------------------


class TestOllamaDetection:
    """Tests for Ollama detection and backend selection."""

    def test_is_ollama_available_when_present(self, manager):
        with patch("shutil.which", return_value="/usr/bin/ollama"):
            assert LocalModelManager.is_ollama_available() is True

    def test_is_ollama_available_when_missing(self, manager):
        with patch("shutil.which", return_value=None):
            assert LocalModelManager.is_ollama_available() is False

    def test_get_backend_ollama(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True):
            assert manager.get_backend() == "ollama"

    def test_get_backend_llama_cpp(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            assert manager.get_backend() == "llama-cpp-python"


class TestOllamaRunning:
    """Tests for Ollama daemon detection."""

    def test_is_ollama_running_true(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock()
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            assert LocalModelManager.is_ollama_running() is True

    def test_is_ollama_running_false(self):
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            assert LocalModelManager.is_ollama_running() is False


class TestOllamaListModels:
    """Tests for listing models from Ollama."""

    def test_list_models_success(self):
        import json
        mock_data = json.dumps({
            "models": [
                {"name": "gemma4:e2b"},
                {"name": "llama3.2:latest"},
            ]
        }).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            models = LocalModelManager.ollama_list_models()
            assert "gemma4:e2b" in models
            assert "llama3.2:latest" in models

    def test_list_models_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            models = LocalModelManager.ollama_list_models()
            assert models == []


class TestOllamaModelPulled:
    """Tests for checking if a model is already pulled in Ollama."""

    def test_model_pulled_exact_match(self, manager):
        with patch.object(LocalModelManager, "ollama_list_models",
                          return_value=["gemma4:e2b", "llama3.2:latest"]):
            assert manager._ollama_model_pulled("gemma4:e2b") is True

    def test_model_not_pulled(self, manager):
        with patch.object(LocalModelManager, "ollama_list_models",
                          return_value=["llama3.2:latest"]):
            assert manager._ollama_model_pulled("gemma4:e2b") is False

    def test_model_pulled_bare_name(self, manager):
        """Bare name matches any tag of that model."""
        with patch.object(LocalModelManager, "ollama_list_models",
                          return_value=["gemma4:e2b"]):
            assert manager._ollama_model_pulled("gemma4") is True


class TestOllamaPull:
    """Tests for pulling models via Ollama."""

    def test_pull_not_installed(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            result = manager.ollama_pull("gemma4:e2b")
            assert result["success"] is False
            assert "not installed" in result["error"].lower()

    def test_pull_starts_thread(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True):
            result = manager.ollama_pull("gemma4:e2b")
            assert result["success"] is True
            assert "pulling" in result["message"].lower()
            # Clean up: wait for thread to finish (it will fail since ollama
            # is not actually installed, but the thread was started)
            if manager._download_thread:
                manager._download_thread.join(timeout=5)


class TestEnsureOllamaRunning:
    """Tests for ensuring the Ollama daemon is running."""

    def test_not_installed(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            result = manager.ensure_ollama_running()
            assert result["success"] is False
            assert "not installed" in result["error"].lower()

    def test_already_running(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True), \
             patch.object(LocalModelManager, "is_ollama_running", return_value=True):
            result = manager.ensure_ollama_running()
            assert result["success"] is True
            assert result["already_running"] is True
            assert result["url"] == _DEFAULT_OLLAMA_URL


class TestKnownModelVariantsHaveOllamaTag:
    """Ensure all known model variants have an Ollama tag."""

    def test_all_variants_have_ollama_tag(self):
        for variant in _KNOWN_MODEL_VARIANTS:
            assert "ollama_tag" in variant, f"Missing ollama_tag for {variant['id']}"
            assert variant["ollama_tag"], f"Empty ollama_tag for {variant['id']}"


class TestDownloadModelPrefsOllama:
    """Tests for download_model preferring Ollama when available."""

    def test_download_uses_ollama_pull_when_available(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True), \
             patch.object(manager, "_ollama_model_pulled", return_value=False), \
             patch.object(manager, "ollama_pull",
                          return_value={"success": True, "message": "pulling..."}) as mock_pull:
            result = manager.download_model("gemma4-e2b-q4_k_m")
            assert result["success"] is True
            mock_pull.assert_called_once_with("gemma4:e2b", None)

    def test_download_already_in_ollama(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True), \
             patch.object(manager, "_ollama_model_pulled", return_value=True):
            result = manager.download_model("gemma4-e2b-q4_k_m")
            assert result["success"] is True
            assert "already" in result["message"].lower()

    def test_download_falls_back_to_gguf(self, manager, tmp_models_dir):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            # Model not downloaded, will try HF download
            (tmp_models_dir / "google_gemma-4-E2B-it-Q4_K_M.gguf").write_bytes(b"fake")
            result = manager.download_model("gemma4-e2b-q4_k_m")
            assert result["success"] is True
            assert "already" in result.get("message", "").lower()


class TestStartServerPrefsOllama:
    """Tests for start_server preferring Ollama when available."""

    def test_start_uses_ollama_when_available(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True), \
             patch.object(manager, "ensure_ollama_running",
                          return_value={"success": True, "url": _DEFAULT_OLLAMA_URL}), \
             patch.object(manager, "_ollama_model_pulled", return_value=True):
            result = manager.start_server("gemma4-e2b-q4_k_m")
            assert result["success"] is True
            assert result["backend"] == "ollama"
            assert result["url"] == _DEFAULT_OLLAMA_URL

    def test_start_falls_back_to_llama_cpp(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            # No GGUF file either → error
            result = manager.start_server("gemma4-e2b-q4_k_m")
            assert result["success"] is False


class TestGetServerStatusWithOllama:
    """Tests for get_server_status with Ollama backend."""

    def test_status_with_ollama_running(self, manager):
        manager._ollama_model_id = "gemma4-e2b-q4_k_m"
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True), \
             patch.object(LocalModelManager, "is_ollama_running", return_value=True):
            status = manager.get_server_status()
            assert status["running"] is True
            assert status["backend"] == "ollama"
            assert status["url"] == _DEFAULT_OLLAMA_URL
            assert status["model_id"] == "gemma4-e2b-q4_k_m"

    def test_status_not_running_no_ollama(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            status = manager.get_server_status()
            assert status["running"] is False
            assert status["backend"] is None


class TestGetAvailableModelsWithOllama:
    """Tests for get_available_models with Ollama backend info."""

    def test_models_show_backend_none_when_not_downloaded(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            models = manager.get_available_models()
            for m in models:
                assert m["backend"] == "none"

    def test_models_show_backend_ollama_when_pulled(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=True), \
             patch.object(LocalModelManager, "ollama_list_models",
                          return_value=["gemma4:e2b"]):
            models = manager.get_available_models()
            e2b = next(m for m in models if m["id"] == "gemma4-e2b-q4_k_m")
            assert e2b["downloaded"] is True
            assert e2b["backend"] == "ollama"

    def test_models_show_backend_gguf_when_file_exists(self, manager, tmp_models_dir):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            (tmp_models_dir / "google_gemma-4-E2B-it-Q4_K_M.gguf").write_bytes(b"fake")
            models = manager.get_available_models()
            e2b = next(m for m in models if m["id"] == "gemma4-e2b-q4_k_m")
            assert e2b["downloaded"] is True
            assert e2b["backend"] == "gguf"

    def test_ollama_tag_present_in_all_known_variants(self, manager):
        with patch.object(LocalModelManager, "is_ollama_available", return_value=False):
            models = manager.get_available_models()
            for m in models:
                if m["id"].startswith("gemma4-"):
                    assert "ollama_tag" in m
