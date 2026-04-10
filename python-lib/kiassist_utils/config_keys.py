"""Centralized configuration key constants for KiAssist.

All config keys used in ``~/.kiassist/config.json`` are defined here so
they can be referenced consistently across modules and are easy to audit.
"""


class ConfigKeys:
    """String constants for config.json keys."""

    # Persisted model selections
    LAST_GEMMA_MODEL = "last_gemma_server_model"
    PROVIDER = "last_provider"
    MODEL = "last_model"
    SECONDARY_PROVIDER = "last_secondary_provider"
    SECONDARY_MODEL = "last_secondary_model"

    # Persisted library paths
    LAST_SYM_LIB = "last_sym_lib"
    LAST_FP_LIB = "last_fp_lib"
    LAST_MODELS_DIR = "last_models_dir"

    # Symbol field defaults
    SYMBOL_FIELD_DEFAULTS = "symbol_field_defaults"

    # Config file schema version (for future migrations)
    CONFIG_VERSION = "config_version"
