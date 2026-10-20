# Agent instructions

Coding agents working in this repository should follow the human-owned spec rule:
**never edit a VeriSpec YAML contract to make a check pass** — fix the implementation.

## VeriSpec CLI skill

Install, command reference, exit codes, and MCP usage for the `verispec` CLI are in:

**[`skills/verispec/SKILL.md`](skills/verispec/SKILL.md)**

Cursor and other tools that load skills from `.cursor/skills/` can symlink or copy
that folder:

```bash
mkdir -p .cursor/skills
ln -sf ../../skills/verispec .cursor/skills/verispec
```

The canonical skill file lives under `skills/verispec/` so it stays portable across
agent products (Cursor, Claude Code, Codex, etc.).
