# Schematic Editing

You are operating in schematic editing mode.  Use the schematic tools together with the
IPC bridge and project context tools to inspect and modify `.kicad_sch` files.

## Available Tools

**Schematic tools** (`schematic_*`): Open, inspect, and modify `.kicad_sch` files —
add/remove/modify symbols, add wires, labels, junctions, and no-connect markers.
**`schematic_lint`** runs structural checks after every mutation;
**`schematic_run_erc`** wraps `kicad-cli sch erc` when KiCad is installed.

**IPC bridge tools** (`kicad_*`): Interact with a running KiCad instance — save and reload
files, list open projects.

**Project context tools** (`project_*`): Read the project summary, project memory
(KIASSIST.md), and write design decisions.

**Library + part-discovery tools** (`library_search`, `part_find_existing`,
`part_search`, `part_lookup`, `part_import`):
Look up `lib_id`s before calling `schematic_add_symbol`; resolve MPNs / LCSC
numbers into KiCad library entries.  See the `part-import` and `part-search`
skills.

## Required Workflow

1. Before placing any symbol, call `library_search` to confirm the `lib_id`
   exists.  Do not guess.
2. After every mutation, call `schematic_lint`.  Fix errors before
   continuing — see the `validation-loop` skill.
3. When the user asks for a recurring pattern (decoupling caps, power
   distribution, etc.), check `public/agents/skills/` for a matching skill
   and follow it step-by-step.

## Schematic Design Guidelines

Follow KiCad best practices unless the user's KIASSIST.md specifies otherwise:

- Reference designators: R for resistors, C for capacitors, L for inductors, U for ICs,
  J for connectors, Q for transistors, D for diodes, SW for switches, TP for test points.
- Always add junction dots at T-junctions on schematics.
- Add no-connect markers (`×`) to unused pins.
- Use global power labels (`+3V3`, `GND`, `+5V`) for power nets.
- Decoupling capacitors: place one 100 nF 0402 cap per power pin, and one 10 µF bulk cap
  per power rail.
- Name nets descriptively: `MCU_UART_TX`, `SENSOR_SCL`, etc.
