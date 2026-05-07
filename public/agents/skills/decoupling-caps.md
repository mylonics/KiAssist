# Skill: Add decoupling capacitors

**Purpose:** Place one 100 nF (0402) decoupling cap per VDD/VCC pin of an IC,
plus one 10 µF bulk cap per power rail, and connect them between the IC's
power pin and `GND`.

**MCP tools used:** `schematic_get_power_pins`, `library_search`,
`schematic_add_symbol`, `schematic_add_wire`, `schematic_add_label`,
`schematic_lint`.

## Steps

1. Call `schematic_get_power_pins(path, reference=<IC ref>)`.  This returns
   each power pin's name and absolute position.  If the result is empty,
   stop and ask the user which pins are power inputs.
2. For each power pin returned:
   1. Call `library_search(query="capacitor", kind="symbol")` once and reuse
      the resulting `lib_id` (typically `Device:C`).
   2. Call `schematic_add_symbol(path, lib_id, x=pin.x + 5.08, y=pin.y,
      reference="C?", value="100n", footprint="Capacitor_SMD:C_0402_1005Metric")`.
   3. Call `schematic_add_wire` from the IC pin to the cap's top pad, then
      from the cap's bottom pad to a point 2.54 mm below.
   4. Call `schematic_add_label(text="GND", x, y, angle=180)` at the bottom
      wire end and `schematic_add_label(text=<rail>, x, y)` at the IC pin
      side (e.g. `+3V3`, `+5V`).
3. After every IC has been processed, add **one** 10 µF bulk cap per
   *unique* power rail seen in step 2.  Use value `10u`, footprint
   `Capacitor_SMD:C_0805_2012Metric`, placed near the connector / regulator
   feeding that rail.
4. Call `schematic_lint(path)` and report the result.

## Done when

- `schematic_lint` returns `success: true` (no duplicate refs, no parse
  errors).
- Every cap has both a `Value` and a `Footprint` set (so no
  `missing_value` / `missing_footprint` warnings appear for the new caps).
- The user has been shown the rail → bulk-cap mapping so they can confirm
  the rail names you inferred match their schematic.

## Notes

- Reference annotation (`C?` → `C12`) is **not** done here; KiCad's annotate
  pass handles it, or the user can call it explicitly.  `schematic_lint`
  will report `unannotated_reference` warnings, which is expected.
- Never delete an existing cap without explicit user confirmation.
