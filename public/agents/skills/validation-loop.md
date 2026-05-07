# Skill: Validation loop

**Purpose:** After any schematic mutation, run validation in a tight loop —
fix what you can, report what you can't.  This is the "compile → error →
fix" cycle.

**MCP tools used:** `schematic_lint`, `schematic_run_erc`,
`schematic_modify_symbol`, `schematic_remove_symbol`.

## When to invoke

- Immediately after **every** call to `schematic_add_symbol`,
  `schematic_remove_symbol`, `schematic_modify_symbol`, `schematic_add_wire`,
  `schematic_connect_pins`, `schematic_add_label`, `schematic_add_junction`,
  or `schematic_add_no_connect`.
- Before reporting "done" to the user at the end of a multi-step task.

## Steps

1. Call `schematic_lint(path)`.  This is fast, pure-Python, always
   available.
2. If `success: false`, walk the `issues` list:
   - `parse_error`: stop, do not attempt automatic fixes — restore the
     `.bak` file and report the error to the user verbatim.
   - `duplicate_reference`: pick the symbol that was added most recently and
     either rename it (call `schematic_modify_symbol` with a new
     `reference`) or remove it (`schematic_remove_symbol`) if it is a
     mistaken duplicate.
   - `missing_value` / `missing_footprint` warnings on symbols **you just
     added**: fill them in via `schematic_modify_symbol`.  Warnings on
     pre-existing symbols may be ignored unless the user asked for a clean
     pass.
3. Re-run `schematic_lint(path)`.  Repeat at most **three times** — if the
   same error keeps appearing, stop and ask the user.
4. Optionally call `schematic_run_erc(path)`.  If the report has
   `available: false`, mention to the user once that installing KiCad's
   `kicad-cli` would enable the authoritative ERC, then continue.
5. Summarise the final lint/ERC status to the user (e.g. *"3 symbols added,
   schematic_lint clean, ERC unavailable in this environment"*).

## Done when

- `schematic_lint` returns `success: true` **or** the only remaining issues
  are `unannotated_reference` warnings (KiCad annotate handles those).
- The user has been told whether ERC ran and, if it did, the error /
  warning counts.

## Anti-patterns to avoid

- Do **not** call `kicad_save_schematic` / `kicad_reload_schematic` between
  every fix — the mutating tools route through `SchematicEditPipeline`
  automatically.
- Do **not** silently swallow lint errors.  If a fix fails, surface the
  remaining errors verbatim.
