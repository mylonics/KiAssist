"""Local LLM model manager for downloading and serving Gemma 4 GGUF models.

This module provides:

* Discovery of available Gemma 4 model variants from Hugging Face Hub.
* Download management with progress tracking.
* Lifecycle control for a local ``llama-cpp-python`` inference server that
  exposes an OpenAI-compatible ``/v1`` API so the existing
  :class:`~kiassist_utils.ai.ollama.OllamaProvider` can connect seamlessly.

Models are downloaded from Hugging Face (public GGUF repos) and stored
under ``~/.kiassist/models/`` by default.  The storage directory can be
overridden via the ``KIASSIST_MODELS_DIR`` environment variable.

Usage example::

    from kiassist_utils.local_llm import LocalModelManager

    mgr = LocalModelManager()

    # List what's available
    for m in mgr.get_available_models():
        print(m["id"], m["size_label"], m["downloaded"])

    # Download a model (blocks, updates progress)
    mgr.download_model("gemma4-4b-q4_k_m")

    # Start serving
    mgr.start_server("gemma4-4b-q4_k_m")
    print(mgr.get_server_status())  # {"running": True, "url": "...", ...}

    # Stop when done
    mgr.stop_server()
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default directory for downloaded models
_DEFAULT_MODELS_DIR = Path.home() / ".kiassist" / "models"

# Hugging Face Hub base URL for resolving model downloads.
# Models are fetched from: https://huggingface.co/{repo_id}/resolve/main/{filename}
_HF_BASE_URL = "https://huggingface.co"

# Port for the local llama-cpp-python server
_DEFAULT_SERVER_PORT = 8741

# How often to poll download progress (bytes between updates)
_PROGRESS_UPDATE_INTERVAL = 1024 * 1024  # 1 MiB


# ---------------------------------------------------------------------------
# Model variant definitions
# ---------------------------------------------------------------------------

# These define the *known* Gemma 4 model variants that KiAssist can download
# and serve.  Each entry points to a public GGUF file on Hugging Face Hub.
# Additional variants discovered at runtime in the models directory are merged in.

_KNOWN_MODEL_VARIANTS: List[Dict[str, Any]] = [
    {
        "id": "gemma4-e2b-q4_k_m",
        "name": "Gemma 4 E2B (Q4_K_M)",
        "filename": "google_gemma-4-E2B-it-Q4_K_M.gguf",
        "hf_repo": "bartowski/google_gemma-4-E2B-it-GGUF",
        "size_label": "~3.5 GB",
        "size_bytes": 3_462_673_376,
        "description": "Smallest variant (5B params) — fast, low memory. Good for quick prototyping.",
        "context_window": 32_768,
    },
    {
        "id": "gemma4-e4b-q4_k_m",
        "name": "Gemma 4 E4B (Q4_K_M)",
        "filename": "google_gemma-4-E4B-it-Q4_K_M.gguf",
        "hf_repo": "bartowski/google_gemma-4-E4B-it-GGUF",
        "size_label": "~5.4 GB",
        "size_bytes": 5_405_163_520,
        "description": "Balanced quality and speed (8B params) — recommended for most use cases.",
        "context_window": 32_768,
    },
    {
        "id": "gemma4-26b-a4b-q4_k_m",
        "name": "Gemma 4 26B-A4B (Q4_K_M)",
        "filename": "google_gemma-4-26B-A4B-it-Q4_K_M.gguf",
        "hf_repo": "bartowski/google_gemma-4-26B-A4B-it-GGUF",
        "size_label": "~17.0 GB",
        "size_bytes": 17_035_033_216,
        "description": "MoE variant (25B params, 4B active) — strong quality, efficient inference.",
        "context_window": 32_768,
    },
    {
        "id": "gemma4-31b-q4_k_m",
        "name": "Gemma 4 31B (Q4_K_M)",
        "filename": "google_gemma-4-31B-it-Q4_K_M.gguf",
        "hf_repo": "bartowski/google_gemma-4-31B-it-GGUF",
        "size_label": "~19.6 GB",
        "size_bytes": 19_598_483_328,
        "description": "Largest variant (31B params) — best quality, requires 24+ GB RAM.",
        "context_window": 32_768,
    },
]


# ---------------------------------------------------------------------------
# Download progress tracking
# ---------------------------------------------------------------------------

@dataclass
class DownloadProgress:
    """Tracks the progress of a model download."""

    model_id: str = ""
    filename: str = ""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_bytes_per_sec: float = 0.0
    eta_seconds: float = 0.0
    status: str = "idle"  # idle | downloading | completed | error | cancelled
    error: str = ""

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, (self.downloaded_bytes / self.total_bytes) * 100)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["percent"] = round(self.percent, 1)
        return d


# ---------------------------------------------------------------------------
# Local Model Manager
# ---------------------------------------------------------------------------

class LocalModelManager:
    """Manages downloading, storing, and serving local Gemma 4 GGUF models.

    Thread-safe.  All public methods are safe to call from any thread.
    """

    def __init__(
        self,
        models_dir: Optional[Path] = None,
        server_port: int = _DEFAULT_SERVER_PORT,
    ) -> None:
        self._models_dir = models_dir or Path(
            os.environ.get("KIASSIST_MODELS_DIR", str(_DEFAULT_MODELS_DIR))
        )
        self._server_port = server_port

        # Ensure models directory exists
        self._models_dir.mkdir(parents=True, exist_ok=True)

        # Download state
        self._download_lock = threading.Lock()
        self._download_progress = DownloadProgress()
        self._download_thread: Optional[threading.Thread] = None
        self._download_cancel = threading.Event()

        # Server state
        self._server_lock = threading.Lock()
        self._server_process: Optional[subprocess.Popen] = None
        self._server_model_id: Optional[str] = None
        self._server_ready = threading.Event()
        self._server_log_file: Optional[Path] = None

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Return all known model variants with download status.

        Each entry contains:

        * ``id`` – unique identifier (e.g. ``"gemma4-4b-q4_k_m"``)
        * ``name`` – human-readable name
        * ``filename`` – GGUF filename on disk / in the release
        * ``size_label`` – human-readable size string
        * ``description`` – one-liner describing the variant
        * ``downloaded`` – whether the file exists locally
        * ``path`` – full path to the local file (may not exist yet)

        This merges the built-in variant list with any extra models
        discovered in the models directory.
        """
        models: List[Dict[str, Any]] = []

        for variant in _KNOWN_MODEL_VARIANTS:
            entry = dict(variant)
            local_path = self._models_dir / variant["filename"]
            entry["downloaded"] = local_path.is_file()
            entry["path"] = str(local_path)
            models.append(entry)

        # Also discover any extra .gguf files in the models directory that
        # are not part of the known variants
        known_filenames = {v["filename"] for v in _KNOWN_MODEL_VARIANTS}
        for gguf_file in sorted(self._models_dir.glob("*.gguf")):
            if gguf_file.name not in known_filenames:
                models.append({
                    "id": gguf_file.stem,
                    "name": gguf_file.stem,
                    "filename": gguf_file.name,
                    "size_label": _human_readable_size(gguf_file.stat().st_size),
                    "size_bytes": gguf_file.stat().st_size,
                    "description": "Manually added model.",
                    "context_window": 32_768,
                    "hf_repo": "",
                    "downloaded": True,
                    "path": str(gguf_file),
                })

        return models

    def get_downloaded_models(self) -> List[Dict[str, Any]]:
        """Return only models that have been downloaded."""
        return [m for m in self.get_available_models() if m.get("downloaded")]

    def _find_variant(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Look up a model variant by ID."""
        for v in _KNOWN_MODEL_VARIANTS:
            if v["id"] == model_id:
                return dict(v)
        # Check extra models on disk
        for gguf_file in self._models_dir.glob("*.gguf"):
            if gguf_file.stem == model_id:
                return {
                    "id": model_id,
                    "filename": gguf_file.name,
                    "hf_repo": "",
                }
        return None

    # ------------------------------------------------------------------
    # Hugging Face download URL resolution
    # ------------------------------------------------------------------

    def _resolve_download_url(self, variant: Dict[str, Any]) -> Optional[str]:
        """Resolve the download URL for a model variant from Hugging Face Hub.

        Uses the standard HF ``/resolve/main/`` endpoint which works for
        public repos without authentication and is backed by a global CDN.

        Returns:
            Direct download URL, or ``None`` if ``hf_repo`` is empty.
        """
        hf_repo = variant.get("hf_repo", "")
        filename = variant["filename"]

        if not hf_repo:
            logger.warning("No hf_repo configured for variant %r", variant.get("id"))
            return None

        url = f"{_HF_BASE_URL}/{hf_repo}/resolve/main/{filename}"
        return url

    # ------------------------------------------------------------------
    # Download management
    # ------------------------------------------------------------------

    def download_model(
        self,
        model_id: str,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> Dict[str, Any]:
        """Start downloading a model variant in the background.

        Args:
            model_id: The variant ID to download.
            progress_callback: Optional callback invoked periodically with
                :class:`DownloadProgress`.

        Returns:
            Result dict with ``success`` and optional ``error``.
        """
        variant = self._find_variant(model_id)
        if not variant:
            return {"success": False, "error": f"Unknown model: {model_id}"}

        local_path = self._models_dir / variant["filename"]
        if local_path.is_file():
            return {"success": True, "message": "Model already downloaded."}

        with self._download_lock:
            if (
                self._download_thread is not None
                and self._download_thread.is_alive()
            ):
                return {
                    "success": False,
                    "error": "Another download is already in progress.",
                }

            self._download_cancel.clear()
            self._download_progress = DownloadProgress(
                model_id=model_id,
                filename=variant["filename"],
                total_bytes=variant.get("size_bytes", 0),
                status="downloading",
            )
            self._download_thread = threading.Thread(
                target=self._download_worker,
                args=(variant, local_path, progress_callback),
                daemon=True,
            )
            self._download_thread.start()

        return {"success": True, "message": "Download started."}

    def _download_worker(
        self,
        variant: Dict[str, Any],
        local_path: Path,
        callback: Optional[Callable[[DownloadProgress], None]],
    ) -> None:
        """Background worker that downloads a GGUF file."""
        url = self._resolve_download_url(variant)
        if not url:
            self._download_progress.status = "error"
            self._download_progress.error = "Could not resolve download URL."
            return

        tmp_path = local_path.with_suffix(".gguf.part")
        start_time = time.monotonic()

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                if total > 0:
                    self._download_progress.total_bytes = total

                downloaded = 0
                last_update = 0

                with open(tmp_path, "wb") as f:
                    while True:
                        if self._download_cancel.is_set():
                            self._download_progress.status = "cancelled"
                            tmp_path.unlink(missing_ok=True)
                            return

                        chunk = resp.read(64 * 1024)  # 64 KiB chunks
                        if not chunk:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)

                        # Update progress
                        self._download_progress.downloaded_bytes = downloaded
                        elapsed = time.monotonic() - start_time
                        if elapsed > 0:
                            speed = downloaded / elapsed
                            self._download_progress.speed_bytes_per_sec = speed
                            if speed > 0 and total > 0:
                                remaining = total - downloaded
                                self._download_progress.eta_seconds = (
                                    remaining / speed
                                )

                        # Invoke callback periodically
                        if callback and (downloaded - last_update) >= _PROGRESS_UPDATE_INTERVAL:
                            last_update = downloaded
                            callback(self._download_progress)

            # Rename tmp -> final
            shutil.move(str(tmp_path), str(local_path))
            self._download_progress.downloaded_bytes = (
                self._download_progress.total_bytes or downloaded
            )
            self._download_progress.status = "completed"
            if callback:
                callback(self._download_progress)

            logger.info("Downloaded model %s to %s", variant["id"], local_path)

        except Exception as exc:
            self._download_progress.status = "error"
            self._download_progress.error = str(exc)
            tmp_path.unlink(missing_ok=True)
            logger.error("Download failed for %s: %s", variant["id"], exc)

    def cancel_download(self) -> Dict[str, Any]:
        """Cancel an in-progress download."""
        self._download_cancel.set()
        return {"success": True, "message": "Download cancellation requested."}

    def get_download_progress(self) -> Dict[str, Any]:
        """Return the current download progress."""
        return self._download_progress.to_dict()

    def delete_model(self, model_id: str) -> Dict[str, Any]:
        """Delete a downloaded model file.

        Args:
            model_id: The variant ID to delete.

        Returns:
            Result dict.
        """
        variant = self._find_variant(model_id)
        if not variant:
            return {"success": False, "error": f"Unknown model: {model_id}"}

        local_path = self._models_dir / variant["filename"]
        if not local_path.is_file():
            return {"success": False, "error": "Model file not found."}

        # Don't delete a model that's currently being served
        with self._server_lock:
            if self._server_model_id == model_id and self._server_process:
                return {
                    "success": False,
                    "error": "Cannot delete a model that is currently being served. Stop the server first.",
                }

        try:
            local_path.unlink()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        """Check whether *port* is already bound on localhost."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _timeout_for_model(self, variant: Dict[str, Any]) -> int:
        """Return a sensible startup timeout (seconds) based on model size."""
        size_bytes = variant.get("size_bytes", 0)
        if size_bytes > 15_000_000_000:  # >15 GB
            return 180
        if size_bytes > 5_000_000_000:   # >5 GB
            return 120
        return 90  # small models still need headroom on slow disks

    def start_server(
        self,
        model_id: str,
        n_ctx: int = 16384,
        n_gpu_layers: int = -1,
    ) -> Dict[str, Any]:
        """Start the local ``llama-cpp-python`` inference server.

        The server exposes an OpenAI-compatible API at
        ``http://localhost:{port}/v1``.

        Args:
            model_id: The variant ID to serve (must be downloaded).
            n_ctx: Context size in tokens (default 16384).
            n_gpu_layers: Number of layers to offload to GPU (-1 = all).

        Returns:
            Result dict with ``url`` on success.
        """
        variant = self._find_variant(model_id)
        if not variant:
            return {"success": False, "error": f"Unknown model: {model_id}"}

        model_path = self._models_dir / variant["filename"]
        if not model_path.is_file():
            return {
                "success": False,
                "error": f"Model not downloaded: {model_id}. Download it first.",
            }

        # ----------------------------------------------------------
        # Pre-flight: make sure llama_cpp.server is importable
        # ----------------------------------------------------------
        try:
            check_cmd = [
                sys.executable, "-c",
                "import llama_cpp.server",
            ]
            subprocess.run(
                check_cmd,
                check=True,
                capture_output=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return {
                "success": False,
                "error": (
                    "llama-cpp-python[server] is not installed.\n"
                    "Install it with:  pip install 'llama-cpp-python[server]'"
                ),
            }

        # ----------------------------------------------------------
        # Pre-flight: check the port is free
        # ----------------------------------------------------------
        if self._is_port_in_use(self._server_port):
            return {
                "success": False,
                "error": (
                    f"Port {self._server_port} is already in use. "
                    "Another server instance may be running, or the port "
                    "is occupied by a different application."
                ),
            }

        startup_timeout = self._timeout_for_model(variant)

        with self._server_lock:
            # Stop any existing server
            self._stop_server_locked()

            try:
                # Build the command to start the llama-cpp-python server
                cmd = [
                    sys.executable,
                    "-m", "llama_cpp.server",
                    "--model", str(model_path),
                    "--host", "127.0.0.1",
                    "--port", str(self._server_port),
                    "--n_ctx", str(n_ctx),
                    "--n_gpu_layers", str(n_gpu_layers),
                ]

                logger.info("Starting local LLM server: %s", " ".join(cmd))

                # Start the server process.
                # IMPORTANT: On Windows, using subprocess.PIPE for
                # stdout/stderr causes a deadlock when the pipe buffer
                # fills up (the server writes a lot of loading output).
                # We redirect to a log file instead so the process
                # never blocks on write, and we can still read stderr
                # for error diagnostics.
                log_file = self._models_dir / "server.log"
                self._server_log_file = log_file
                log_fh = open(log_file, "w", encoding="utf-8", errors="replace")

                kwargs: Dict[str, Any] = {
                    "stdout": log_fh,
                    "stderr": subprocess.STDOUT,
                }
                if sys.platform == "win32":
                    kwargs["creationflags"] = (
                        subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                    )

                self._server_process = subprocess.Popen(cmd, **kwargs)
                # Close our copy of the file handle; the child owns it now.
                log_fh.close()
                self._server_model_id = model_id
                self._server_ready.clear()

                # Wait for the server to become ready (poll health endpoint)
                ready = self._wait_for_server(timeout=startup_timeout)
                if not ready:
                    # Capture stderr from the still-running (or dead) process
                    stderr_tail = self._read_server_stderr(max_lines=30)
                    self._stop_server_locked()
                    detail = (
                        f"\nServer stderr (last lines):\n{stderr_tail}"
                        if stderr_tail
                        else ""
                    )
                    return {
                        "success": False,
                        "error": (
                            f"Server failed to start within {startup_timeout} seconds. "
                            "The model may be too large for available memory, "
                            "or a dependency is missing.\n"
                            "Ensure llama-cpp-python[server] is installed: "
                            "pip install 'llama-cpp-python[server]'"
                            f"{detail}"
                        ),
                    }

                url = f"http://127.0.0.1:{self._server_port}/v1"
                self._server_ready.set()

                logger.info("Local LLM server ready at %s", url)
                return {"success": True, "url": url, "model_id": model_id}

            except FileNotFoundError:
                return {
                    "success": False,
                    "error": (
                        "llama-cpp-python[server] is not installed. "
                        "Install it with: pip install 'llama-cpp-python[server]'"
                    ),
                }
            except Exception as exc:
                self._stop_server_locked()
                return {"success": False, "error": str(exc)}

    def _read_server_stderr(self, max_lines: int = 30) -> str:
        """Read up to *max_lines* of recent output from the server log file."""
        log_file = self._server_log_file
        if not log_file or not log_file.is_file():
            return ""
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            return "\n".join(lines[-max_lines:])
        except Exception:
            return ""

    def _wait_for_server(self, timeout: float = 60) -> bool:
        """Poll the server health endpoint until it responds or timeout."""
        url = f"http://127.0.0.1:{self._server_port}/v1/models"
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            # Check if process died
            if self._server_process and self._server_process.poll() is not None:
                stderr = self._read_server_stderr()
                logger.error("Server process exited early. stderr: %s", stderr)
                return False

            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                pass

            time.sleep(1)

        logger.warning(
            "Server did not respond within %s seconds on port %s",
            timeout,
            self._server_port,
        )
        return False

    def stop_server(self) -> Dict[str, Any]:
        """Stop the local LLM server if running."""
        with self._server_lock:
            return self._stop_server_locked()

    def _stop_server_locked(self) -> Dict[str, Any]:
        """Stop the server (caller must hold ``_server_lock``)."""
        if self._server_process is None:
            return {"success": True, "message": "No server running."}

        try:
            # Graceful shutdown
            if sys.platform == "win32":
                self._server_process.terminate()
            else:
                self._server_process.send_signal(signal.SIGTERM)

            try:
                self._server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
                self._server_process.wait(timeout=5)

            logger.info("Local LLM server stopped.")
        except Exception as exc:
            logger.warning("Error stopping server: %s", exc)
        finally:
            self._server_process = None
            self._server_model_id = None
            self._server_ready.clear()
            self._server_log_file = None

        return {"success": True}

    def get_server_status(self) -> Dict[str, Any]:
        """Return the current server status.

        Checks both the tracked subprocess *and* the actual health
        endpoint, so orphan servers from a previous session are detected.

        Returns:
            Dictionary with ``running``, ``url``, ``model_id``, ``port``.
        """
        with self._server_lock:
            running = (
                self._server_process is not None
                and self._server_process.poll() is None
            )
            if not running and self._server_process is not None:
                # Process has exited unexpectedly
                self._server_process = None
                self._server_model_id = None
                self._server_ready.clear()

        # If we don't own a process, probe the port to detect an orphan
        # server left behind by a previous session.
        if not running and self._is_port_in_use(self._server_port):
            try:
                url = f"http://127.0.0.1:{self._server_port}/v1/models"
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        running = True
                        logger.info(
                            "Detected orphan LLM server on port %s",
                            self._server_port,
                        )
            except Exception:
                pass

        return {
            "running": running,
            "url": f"http://127.0.0.1:{self._server_port}/v1" if running else None,
            "model_id": self._server_model_id if running else None,
            "port": self._server_port,
        }

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def models_dir(self) -> Path:
        """Path to the local models directory."""
        return self._models_dir

    @property
    def server_port(self) -> int:
        """Port the local server listens on."""
        return self._server_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _human_readable_size(size_bytes: int) -> str:
    """Convert byte count to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} PB"
