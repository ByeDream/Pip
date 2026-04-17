# Pip-Boy

[![CI](https://github.com/ByeDream/Pip-Boy/actions/workflows/ci.yml/badge.svg)](https://github.com/ByeDream/Pip-Boy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pip-boy)](https://pypi.org/project/pip-boy/)
[![Python](https://img.shields.io/pypi/pyversions/pip-boy)](https://pypi.org/project/pip-boy/)
[![License](https://img.shields.io/github/license/ByeDream/Pip-Boy)](LICENSE)

<p align="center">
  <img src="docs/Imgs/Pip-BoyAdArtPrint.jpg" width="480" alt="Pip-Boy 3000 Mark IV" />
</p>

A personal assistant agent with persistent memory, multi-channel support, and a configurable persona. Built on Anthropic's Claude API, it supports multi-agent teamwork, task planning, git worktree isolation, and extensible skills — accessible via CLI, WeChat, or WeCom.

## Features

### Core

- **Conversational REPL** — Interactive chat loop with readline history and UTF-8 support
- **Persona System** — Lead persona ("Pip-Boy") with customizable teammate personas via Markdown + YAML frontmatter
- **Multi-Channel** — CLI, WeChat (personal), and WeCom (enterprise) channels with unified message routing
- **Web Search** — Tavily integration with automatic DuckDuckGo fallback

### Memory System

A three-tier pipeline that learns from conversations automatically:

- **L1 Reflect** — Extracts behavioral observations (user preferences, decision patterns) and objective experience (technical lessons, API insights, reusable patterns) from conversation transcripts
- **L2 Consolidate** — Merges observations into memories with reinforcement, decay, and conflict resolution
- **L3 Axiom Distillation** — Promotes high-stability memories into judgment principles (`axioms.md`)
- **Dream Cycle** — L2 + L3 run together at a configurable hour when the system is idle and enough observations have accumulated
- **Memory Recall** — TF-IDF search with temporal decay injects relevant memories into the system prompt
- **Reflect Tool** — The agent can proactively trigger reflection when meaningful work is completed
- **SOP-Driven Prompts** — Memory pipeline rules are maintained in an external [SOP document](src/pip_agent/memory/sops/memory_pipeline_sop.md) for easy tuning

### User Identity

- **Owner Profile** — `owner.md` is read-only and defines the workspace owner with channel identifiers
- **User Profiles** — `remember_user` tool creates and updates profiles for other users (`users/*.md`)
- **ACL** — Owner and admin roles control access to sensitive operations (e.g., `/clean`, `/reset`)
- **Multi-Channel Identity** — Users are tracked by channel-specific identifiers (WeChat ID, WeCom ID, CLI)

### Tools

- **Filesystem** — `read`, `write`, `edit`, `glob` (sandboxed to working directory)
- **Shell** — `bash` execution with optional **background mode** for long-running commands
- **Web** — `web_search` and `web_fetch` for real-time information retrieval
- **Memory** — `memory_search` for explicit recall, `reflect` for on-demand reflection
- **Skills** — `load_skill` dynamically loads built-in and user-defined skill guides

### Task Planning

- **Story / Task DAG** — Two-level planning: stories (epics) contain tasks with dependency tracking
- **Kanban Board** — `task_board_overview`, `task_board_detail` for status visualization
- **State Machine** — Tasks flow through `pending` → `in_progress` → `in_review` → `completed` / `failed`
- **Persistent Storage** — JSON files under `.pip/agents/<id>/tasks/` survive across sessions

### Multi-Agent Team

- **Teammate Spawning** — `team_spawn` creates daemon threads with per-session model and turn limits
- **Inbox Messaging** — JSONL-based message bus (`send`, `read_inbox`) between lead and teammates
- **Model Selection** — Per-project `.pip/models.json` defines available models for teammate assignment
- **Per-Agent Isolation** — Each agent has its own data directory, TeamManager, and WorktreeManager
- **CLI Commands** — `/team` for roster status, `/inbox` to peek the lead inbox

### Git Worktree Isolation

- **Isolated Branches** — Each subagent works in its own git worktree (`.pip/.worktrees/{name}/`, branch `wt/{name}`)
- **Sync / Integrate / Cleanup** — Worktree lifecycle management with merge conflict detection
- **Task Submission** — `task_submit` syncs work and transitions task status automatically

### Context Management

- **Micro-Compaction** — Old tool results replaced with placeholders, keeping the last N rounds intact
- **Auto-Compaction** — When token count exceeds threshold, the oldest ~50% is summarised by the LLM while the recent tail (~20%, minimum 4 messages) is preserved verbatim
- **Overflow Recovery** — On a context-overflow API error, `emergency_compact` runs a three-stage fallback (aggressive micro-compact → oversized tool_result truncation → tail-preserving summary) and retries with the same profile
- **Transcript Persistence** — Every conversation turn saves a timestamped JSON transcript; old transcripts are cleaned up after reflection

### Resilience

Every `messages.create` call is wrapped in a three-layer retry onion (`pip_agent.resilience`):

- **Layer 1 — Auth Rotation** — Iterate through available profiles (`.env::ANTHROPIC_API_KEY` as baseline + any extras in `.pip/keys.json`), skipping any in cooldown. Failures classify as `rate_limit` (120s), `auth` / `billing` (300s), `timeout` (60s), or `unknown` (120s).
- **Layer 2 — Overflow Recovery** — On context overflow, `emergency_compact` mutates the message list in place and retries up to 3 times with the same profile.
- **Layer 3 — Tool-Use Loop** — The standard `while True + stop_reason` loop; each iteration is one Layer-1 call.
- **Fallback Models** — After all primary profiles are exhausted, Pip-Boy tries each model in `fallback_models` (per-agent YAML) before raising `ResilienceExhausted`.
- **Simulated Failures** — `/simulate-failure <reason>` arms the next API call to fail with a given category, letting you verify the retry path without real outages.

### Built-in Skills

| Skill | Purpose |
|-------|---------|
| `task-planning` | Structured planning with story/task breakdown |
| `agent-team` | Multi-agent coordination and delegation |
| `git` | Git operations and workflow guidance |
| `code-review` | Code review methodology |
| `create-skill` | Authoring new custom skills |

## Installation

**Prerequisites:** Python >= 3.11

```bash
pip install pip-boy
```

### Development (from source)

```bash
git clone https://github.com/ByeDream/Pip-Boy.git
cd Pip-Boy
pip install -e ".[dev]"
```

## Usage

```bash
# Navigate to your target project and run
cd /path/to/your/project
pip-boy

# CLI-only mode (no WeChat/WeCom channels)
pip-boy --cli

# Force WeChat QR login
pip-boy --scan

# Show version
pip-boy --version
```

On first launch, the scaffold automatically creates the `.pip/` directory structure, `.env` (from template), and `.gitignore` entries. Edit the generated `.env` to fill in your `ANTHROPIC_API_KEY`, then run again.

The agent uses `Path.cwd()` as its working directory — always run it from the project you want to interact with.

### Updating

From within a running session:

```
/update
```

Or manually:

```bash
pip install --upgrade pip-boy
```

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Conditional | — | Primary Anthropic API key; loaded as profile `env` and always serves as the baseline |
| `ANTHROPIC_BASE_URL` | No | *(api.anthropic.com)* | Custom API endpoint (proxy support); applied to the `env` profile and inherited by any `keys.json` profile that omits its own `base_url` |
| `KEYS_FILE_PATH` | No | `.pip/keys.json` | Override the additional-profiles file location |
| `SEARCH_API_KEY` | No | — | Tavily API key; falls back to DuckDuckGo |
| `WECOM_BOT_ID` | No | — | WeCom bot ID for enterprise WeChat channel |
| `WECOM_BOT_SECRET` | No | — | WeCom bot secret |
| `VERBOSE` | No | `true` | Verbose output |
| `PROFILER_ENABLED` | No | `false` | Enable performance profiling |

At least one of `ANTHROPIC_API_KEY` (in `.env`) or a populated `.pip/keys.json` profile must be present.

#### Memory Pipeline

| Variable | Default | Description |
|---|---|---|
| `REFLECT_TRANSCRIPT_THRESHOLD` | `10` | New transcripts needed to trigger reflection |
| `TRANSCRIPT_RETENTION_DAYS` | `7` | Days to keep reflected transcripts |
| `DREAM_HOUR` | `2` | Local hour (0-23) for the Dream cycle |
| `DREAM_MIN_OBSERVATIONS` | `20` | Minimum observations before Dream can run |
| `DREAM_INACTIVE_MINUTES` | `30` | Agent idle time (minutes) required for Dream |

### Per-Agent Configuration

Model, token limits, compaction settings, and the fallback model chain are configured per-agent via YAML frontmatter in `.pip/agents/<id>/persona.md`:

```yaml
---
model: claude-sonnet-4-6
max_tokens: 8192
compact_threshold: 50000
compact_micro_age: 3
fallback_models:
  - claude-haiku-4-5
  - claude-sonnet-4-5
---
```

`fallback_models` is optional. When set, Pip-Boy falls back through each listed model after every primary profile has failed, before giving up.

### Multi-Key Profiles (`.pip/keys.json`)

The `.env::ANTHROPIC_API_KEY` is always the **baseline** (loaded as profile `env`). `.pip/keys.json` is **additive** — each filled entry is appended as an extra profile for rotation:

```json
{
  "profiles": [
    { "name": "backup", "api_key": "sk-ant-...", "base_url": "" }
  ]
}
```

Pip-Boy scaffolds `.pip/keys.json` on first run with a blank `api_key` placeholder; entries with empty `api_key` are silently ignored, so the untouched template is a no-op. Fill in real keys to enable rotation. A profile that omits `base_url` inherits `.env::ANTHROPIC_BASE_URL` (convenient when all keys share the same proxy). Profiles are de-duplicated by `api_key`, so an entry that happens to equal the env key is skipped with a debug log.

Rotation honours per-reason cooldowns — `rate_limit` 120s, `auth` / `billing` 300s, `timeout` 60s. The file is covered by `.gitignore`.

### Slash Commands (Resilience)

| Command | Description |
|---|---|
| `/profiles` | List all loaded API profiles, their availability, and last-good timestamp |
| `/cooldowns` | Show profiles currently in cooldown with remaining seconds |
| `/stats` | Print resilience runner counters (attempts, successes, rotations, compactions, fallbacks) |
| `/simulate-failure <reason>` | Arm a fake failure (`rate_limit`, `auth`, `timeout`, `billing`, `overflow`, `unknown`) for the next API call; use `off` to disarm |
| `/fallback` | Show the current agent's primary + fallback model chain |

### Project Directory Structure

```
.pip/
├── owner.md                     # Owner profile (read-only)
├── models.json                  # Model catalog for team spawning
├── keys.json                    # Extra rotation profiles layered on top of .env (gitignored)
├── .scaffold_manifest.json      # Scaffold version tracking
├── agents/
│   ├── bindings.json            # Channel → agent routing
│   └── pip-boy/                 # Per-agent directory
│       ├── persona.md           # Agent persona + config (YAML frontmatter)
│       ├── state.json           # Memory pipeline state
│       ├── memories.json        # L2 consolidated memories
│       ├── axioms.md            # L3 judgment principles
│       ├── observations/        # L1 observation files (.jsonl)
│       ├── transcripts/         # Conversation transcripts (.json)
│       ├── users/               # User profiles (.md)
│       ├── tasks/               # Task board state
│       └── team/                # Teammate data + inbox
└── .worktrees/                  # Git worktree isolation
```

## Dependencies

- [`anthropic`](https://github.com/anthropics/anthropic-sdk-python) — Claude API client
- [`pydantic-settings`](https://github.com/pydantic/pydantic-settings) — Configuration management
- [`tavily-python`](https://github.com/tavily-ai/tavily-python) — Web search API
- [`ddgs`](https://github.com/deedy5/duckduckgo_search) — DuckDuckGo fallback search
- [`pyyaml`](https://github.com/yaml/pyyaml) — YAML parsing for skills and personas
- [`httpx`](https://github.com/encode/httpx) — HTTP client for channel communication
- [`wecom-aibot-python-sdk`](https://pypi.org/project/wecom-aibot-python-sdk/) — WeCom enterprise bot SDK
- [`qrcode`](https://github.com/lincolnloop/python-qrcode) — Terminal QR code rendering for WeChat login
- [`pyreadline3`](https://github.com/pyreadline3/pyreadline3) — Readline for Windows

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
