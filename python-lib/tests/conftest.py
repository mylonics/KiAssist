"""Pytest configuration and shared fixtures.

This conftest stubs out optional heavy dependencies (pywebview) so that modules
like ``kiassist_utils.main`` can be imported in the CI environment where those
packages are not installed.
"""

from __future__ import annotations

import sys
import types


def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# Stub out pywebview before any test module imports kiassist_utils.main.
# webview is only needed at runtime (to open a browser window); tests mock
# it away entirely.
# ---------------------------------------------------------------------------
if "webview" not in sys.modules:
    _webview = _make_stub_module(
        "webview",
        Window=type("Window", (), {}),
        create_window=lambda *a, **kw: None,
        start=lambda *a, **kw: None,
    )
    sys.modules["webview"] = _webview

# Force-import kiassist_utils.main so that sys.modules["kiassist_utils.main"]
# is populated before test_main_api.py is collected (it accesses the module
# via sys.modules at collection time).
import kiassist_utils  # noqa: E402 -- must come after stub setup
_ = kiassist_utils.KiAssistAPI  # trigger lazy load
