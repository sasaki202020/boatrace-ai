# Claude Code Bootstrap

Use this repo with Claude Code as the implementation agent.

## Run

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\start_claude_code.ps1
```

## Local Ollama Mode

If you want Claude Code to talk to a local Ollama model:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\start_claude_code.ps1 -UseLocalOllama -Model qwen2.5-coder:7b
```

## Project Rules

- Read `CLAUDE.md` first.
- Read `tasks/CURRENT_TASK.md` before editing.
- Keep changes small and leakage-safe.
- Prefer the existing `src/`, `scripts/`, `commands/`, and `.agents/` structure.
