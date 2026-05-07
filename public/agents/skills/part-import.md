# Skill: Import a part

**Purpose:** Resolve an MPN, supplier PN, or LCSC number into a KiCad
symbol + footprint + 3D model, write them into a project library, and
report what was found.

**MCP tools used:** `part_lookup`, `part_import`.

## When to invoke

- The user asks to "import this part", "find a footprint for `<MPN>`",
  "add `<MPN>` to my library", or pastes a LCSC number (e.g. `C8734`).

## Steps

1. **Identify the search term.**  Extract one of:
   - Manufacturer Part Number (MPN), e.g. `STM32F103C8T6`.
   - Supplier Part Number (SPN), e.g. `497-6063-1-ND` (Digi-Key).
   - LCSC number, e.g. `C8734` (always starts with `C` followed by digits).
2. **Look up first.**  Call `part_lookup(query=<term>)`.  This queries
   Octopart / JLCPCB and returns the cross-referenced part identifiers
   (MPN, manufacturer, datasheet, DKPN, MSPN, LCSC PN) **without writing
   any files**.  Show the user what was found and ask for confirmation
   when ambiguous (multiple manufacturers, etc.).
3. **Import.**  Once confirmed, call `part_import` with the most specific
   identifier available — pass `lcsc` when known, otherwise `mpn`.
   Provide `target_sym_lib` and `target_fp_lib_dir` if the user has named
   a destination library; omit them to drop the imported files into the
   current project's default `kiassist_imports.kicad_sym` /
   `kiassist_imports.pretty/`.
4. **Report.**  The result includes `success`, `warnings`, the resolved
   `lib_id`, and the list of files written.  If `success: false`, surface
   `error` verbatim and offer alternatives:
   - "Try the LCSC number directly if you have one — it bypasses
     Octopart."
   - "Octopart sometimes echoes the MPN as the DKPN; try the Digi-Key
     SPN instead."

## Done when

- The user has the resolved `lib_id` (e.g. `kiassist_imports:STM32F103C8T6`)
  and can hand it to `schematic_add_symbol`.
- All warnings have been surfaced (e.g. "no 3D model on EasyEDA — manual
  step required").

## Notes

- `part_import` is rate-limited (Octopart 2 s, Digi-Key 5 s, JLCPCB 2 s)
  per-service; do not parallelise calls to the same service.
- This skill never deletes existing library entries.  If a part with the
  same name already exists, it is renamed with a numeric suffix unless
  the caller passes `overwrite=true`.
