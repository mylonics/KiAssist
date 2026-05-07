# Skills

This directory holds focused **skill files** that the KiAssist agent can be
asked to follow for specific tasks.  Each `<name>.md` file is a short,
imperative recipe for one concrete operation — e.g. *"add decoupling caps to
this IC"* or *"validate the current schematic in a tight loop"*.

Skills are deliberately narrower than the per-mode agent files (`schematic-agent.md`,
`pcb-agent.md`, …):

* An **agent file** describes the tools available in a mode and the broad
  conventions to follow.
* A **skill file** describes a single workflow end-to-end, including the
  *exact* sequence of MCP tool calls and the expected outputs at each step.

Skills are loaded on-demand by the agent loop when a user request matches a
skill's trigger phrases — the agent quotes the skill body into its working
context for that turn only, keeping the base system prompt small.

## Available skills

| Skill                | Trigger                                             |
|----------------------|-----------------------------------------------------|
| `decoupling-caps`    | "add decoupling caps", "add bypass caps to U?"      |
| `part-import`        | "import this part", "find a footprint for `<MPN>`"  |
| `validation-loop`    | "check my schematic", "fix the ERC errors"          |

## Authoring guidelines

1. One skill per file; ≤ 80 lines.
2. Start with a single-sentence purpose.
3. List the *exact* MCP tools used.
4. Provide a numbered step list.  Each step must be one tool call or one
   decision.
5. End with a "Done when" checklist that mirrors what
   :func:`kiassist_utils.validation.schematic_lint` would report.
