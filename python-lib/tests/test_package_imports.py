"""Tests for top-level package import behavior."""

from __future__ import annotations

import importlib
import sys


def test_gemini_module_is_lazy_loaded_from_package():
    sys.modules.pop("kiassist_utils.gemini", None)

    import kiassist_utils

    importlib.reload(kiassist_utils)
    assert "kiassist_utils.gemini" not in sys.modules

    _ = kiassist_utils.GeminiAPI
    assert "kiassist_utils.gemini" in sys.modules
