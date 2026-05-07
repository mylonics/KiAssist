# Skill: Parametric part search

**Purpose:** Help the user find a real, verifiable part that matches a
set of free-text specifications (e.g. *"24-bit ADC, SPI, single-supply
3.3 V, ≤ $10"*), present a structured top-5 the user can pick from, and
hand the chosen part off to the existing import + schematic flow.

**MCP tools used:** `part_find_existing`, `part_search`, `part_lookup`,
`part_import`, `library_search`, `schematic_add_symbol`.

## When to invoke

- The user asks something like "find me an ADC with 24 bits", "I need a
  buck converter that does 5 V → 3.3 V at 1 A", "what's a cheap I²C
  EEPROM I can use here?".
- Distinguish from the `part-import` skill: that skill resolves a
  *known* MPN/LCSC/SPN.  This skill is for the case where the user does
  **not** yet have a part number.

## Required workflow

### 1. Reuse-first — what does the user already have?

Before searching the web, call

```
part_find_existing(project_path=<current project>, query=<user specs>)
```

If `has_matches: true`, present the matches **first**, grouped into
"already on your schematic" (`schematic_matches`, includes the
`reference` and `schematic` path) and "already in your project library"
(`library_matches`, includes the resolved `lib_id` you can hand to
`schematic_add_symbol` immediately).  Ask the user if any of these fit
before falling through to the web search.

### 2. Discover candidate MPNs

Call `part_search` with just the specs:

```
part_search(specs=<user specs>)
```

The tool returns a `search_id`, a `web_results` list of hits, and a
fuzzy `mpn_hints` list.  **Your job** as the agent is to read the
snippets and extract the genuine MPNs (the heuristic hints are only a
fallback — prefer your own extraction when the snippets are clear).

### 3. Enrich the top candidates

Re-call `part_search` with the MPNs and the same `search_id`:

```
part_search(
  specs=<user specs>,
  search_id=<from step 2>,
  candidate_mpns=["ADS1256IDBT", "LTC2440CGN", ...],
  limit=5,
)
```

Each returned card has `mpn`, `manufacturer`, `description`,
`datasheet_url`, `product_url`, `digikey_pn`/`lcsc_pn`/`mouser_pn`, and
a `verified` flag (false when Octopart had no record of the MPN).

Present them as a numbered list (option 1…5).  For every card, include:

- the MPN and manufacturer,
- the most relevant key specs synthesised from your own knowledge plus
  the description (note these are *your* synthesis — flag any spec you
  cannot back from the datasheet),
- the datasheet link (`datasheet_url`),
- the product page link (`product_url`).

If `verified_all: false`, warn the user that Octopart could not
cross-check every option.

### 4. Refine on user feedback

Common refinement asks:

- **"Narrow further" / new constraint** — re-call `part_search` with
  the same `search_id` and the updated `specs` (or `refine`) string,
  re-extract MPNs from fresh `web_results`, re-enrich.
- **"Cheaper" / "lower power" / "smaller package"** — same loop;
  include the constraint in `refine` so the session log keeps context.
- **"Similar to option 2 but with a faster sample rate"** — keep the
  pinned candidate's MPN in `candidate_mpns` and add new MPNs you
  search for around its specs, all under the same `search_id`.

### 5. Selection → preview → import

Once the user picks an option:

1. **Preview (lookup-only)**: call `part_import` with no target paths.
   This fetches the symbol, footprint and 3D model into a temporary
   directory and returns metadata + warnings (e.g. "no 3D model on
   EasyEDA") **without writing anything to the project**.
2. Show the user what would be imported (symbol pin count, footprint,
   warnings) and the datasheet link.
3. **Commit on confirmation**: re-call `part_import` with the project's
   default destination (omit `target_sym_lib` / `target_fp_lib_dir` to
   get the standard `kiassist_imports.kicad_sym` /
   `kiassist_imports.pretty/`, matching the `part-import` skill).
   Surface the resolved `lib_id` from the response.
4. Offer (do **not** assume) to call `schematic_add_symbol` with the
   new `lib_id`.  This satisfies anti-hallucination rule #1 because the
   `lib_id` was just returned by a tool in the same conversation.

## Done when

- The user has either:
  - confirmed a reuse from the project / library and the `lib_id` was
    handed to `schematic_add_symbol`, **or**
  - selected a new candidate that has been imported and the resulting
    `lib_id` was reported (and optionally placed on the schematic).

## Notes

- `part_search` performs **no LLM reasoning** itself — all extraction
  and ranking is your job as the agent.  The tool is a deterministic
  Octopart / DuckDuckGo / JLCPCB enricher.
- `part_search` rate-limits Octopart at 2 s per request (shared with
  every other `part_*` tool); avoid spawning parallel calls for the
  same service.
- Never invent an MPN or a `lib_id`.  If `verified: false` for a
  candidate, treat it as suspect and ask the user before importing.
