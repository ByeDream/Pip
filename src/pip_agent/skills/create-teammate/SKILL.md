---
name: create-teammate
description: >-
  Guide users through creating new teammate definitions for the agent team system.
  Use when the user wants to create, define, or add a new teammate.
tags: [team, authoring]
---

# Creating Teammates

Help the user create a teammate definition file. Follow the workflow below.

## Phase 1: Gather Requirements

Before writing, clarify with the user:

1. **Role**: What expertise or personality should this teammate have?
2. **Use case**: What kinds of tasks will be delegated to this teammate?
3. **Tools**: Which tools does the teammate need? (bash, read, write, edit, glob, web_search, web_fetch)
4. **Model/cost**: Should it use a cheaper/faster model, or the default?

If you have conversation context, infer the role from what was discussed.

## Phase 2: Create the Definition

### File location

Teammate definitions are `.md` files in `.pip/team/`:

```
.pip/team/
  alice.md
  bob.md
```

The filename stem must match the `name` field in frontmatter.

### Definition format

```markdown
---
name: teammate-name
description: "Brief description of role and when to use this teammate."
model: claude-sonnet-4-6
max_turns: 20
tools: [bash, read, write, edit, glob]
---

You are {name}, a {role} on a collaborative agent team.

### Expertise
- Area 1
- Area 2

### Communication
- Use `send` to report results to `lead` when done.
- Use `read_inbox` to check for new messages during long tasks.
```

### Frontmatter fields

| Field | Required | Rules |
|-------|----------|-------|
| `name` | Yes | Lowercase, hyphens/underscores, must match filename stem. |
| `description` | Yes | Describes WHAT the teammate does and WHEN to use it. |
| `model` | No | LLM model. Omit to use the default model. |
| `max_turns` | No | Max LLM rounds per message batch. Default: 15. |
| `tools` | No | Tool allowlist. Default: bash, read, write, edit, glob, web_search, web_fetch. |

### Writing good descriptions

The description is shown in `team_status`. Write in third person:

- **Good**: "Python backend developer. Use for API design, database work, and backend logic."
- **Bad**: "I am a Python developer."

### Tool selection guidance

- **Researcher**: `[read, glob, web_search, web_fetch]` -- read-only, safe
- **Developer**: `[bash, read, write, edit, glob]` -- full code editing
- **Reviewer**: `[read, glob]` -- read-only analysis
- **DevOps**: `[bash, read, write, glob]` -- infrastructure work

### Model selection guidance

- Default model: good for complex reasoning tasks
- Cheaper/faster model: good for simple, repetitive tasks (e.g. formatting, file operations)
- Specify `model` only when you want to override the default

## Phase 3: Write the File

Use the `write` tool to create `.pip/team/{name}.md` with the content.

## Phase 4: Verify

After writing, confirm:

- [ ] Filename matches `name` field
- [ ] `description` includes WHAT and WHEN
- [ ] `tools` list is minimal for the role (principle of least privilege)
- [ ] Body prompt gives clear identity and communication instructions
- [ ] Suggest `team_activate` to bring the teammate online
