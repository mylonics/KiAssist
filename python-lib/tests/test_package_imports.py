"""Tests for top-level package import behavior."""

from __future__ import annotations

import importlib
import sys


def test_gemini_module_is_lazy_loaded_from_package():
    original_kiassist = sys.modules.get("kiassist_utils")
    original_gemini = sys.modules.get("kiassist_utils.gemini")
    sys.modules.pop("kiassist_utils.gemini", None)
    try:
        kiassist_utils = sys.modules.get("kiassist_utils")
        if kiassist_utils is None:
            kiassist_utils = importlib.import_module("kiassist_utils")
        kiassist_utils = importlib.reload(kiassist_utils)
        assert "kiassist_utils.gemini" not in sys.modules

        _ = kiassist_utils.GeminiAPI
        assert "kiassist_utils.gemini" in sys.modules
    finally:
        if original_kiassist is not None:
            sys.modules["kiassist_utils"] = original_kiassist
        if original_gemini is not None:
            sys.modules["kiassist_utils.gemini"] = original_gemini
        else:
            sys.modules.pop("kiassist_utils.gemini", None)
