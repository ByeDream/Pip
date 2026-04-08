---
name: agent-team
description: >-
  Complete guide for managing the Agent Team: creating teammates,
  spawning with model/turn selection, communication, task coordination,
  and lifecycle management. Load when delegating work to teammates.
tags: [team, management, collaboration]
---

# Agent Team Management

## Teammate Definitions

Teammates are defined as `.md` files in `.pip/team/`. Each file contains:

```markdown
---
name: alice
description: "Python developer. Writes clean code and tests."
---

You are Alice, a Python developer on a collaborative agent team.

### Expertise
- Writing idiomatic Python
- Unit testing with pytest

### Communication
- Use `send` to report results to `lead` when done.
- Use `read_inbox` to check for messages during long tasks.
```

### Managing Definitions

- `team_create(name, description, system_prompt)` — create a new teammate
- `team_edit(name, description?, system_prompt?)` — update an existing teammate
- `team_delete(name)` — remove a teammate definition
- `team_status` — list all teammates and their current state

Create teammates whose roles match the work at hand. A good teammate
definition has a clear identity, specific expertise, and communication
instructions.

## Spawning

```
team_spawn(name, prompt, model, max_turns)
```

All four parameters are required:

- **name**: must exist in the roster (use `team_status` to check)
- **prompt**: detailed task instructions
- **model**: use `team_list_models` to see available models.
  Pick stronger models for complex reasoning, cheaper models for
  simple/repetitive tasks.
- **max_turns**: tool-use rounds budget. Allocate more turns for
  complex tasks.

The teammate begins working immediately on its own thread.

## Communication

### Lead to teammate
- `team_send(to, content)` — direct message
- `team_send(to, content, msg_type="broadcast")` — message all active teammates
- `team_send(to, content, msg_type="shutdown_request")` — ask a teammate to shut down

### Reading responses
- `team_read_inbox` — drain and read all pending messages from teammates
- Messages also appear automatically in your context between tool rounds

### Teammate to lead
Teammates use `send(to="lead", content)` to report back.

## Task Board Coordination

When tasks are planned, teammates can see and claim work:

1. Teammates use `task_board_overview` to see available stories and ready tasks
2. `task_board_detail(story, task_id)` to inspect a specific task
3. `claim_task(story, task_id)` to take ownership and start working

When a teammate goes idle, the system hints if new claimable work appears.

## Lifecycle

```
Spawned → Working → Idle ⇄ Working → Offline
```

- **Working**: actively calling tools and the LLM
- **Idle**: waiting for inbox messages or task board changes (60s timeout)
- **Offline**: finished — either task complete, max_turns exhausted,
  idle timeout, or shutdown approved

When `max_turns` is exhausted, the teammate notifies lead and goes offline.
Re-spawn to continue.

## Workflow Example

1. Plan the work with `task_create`
2. Create specialized teammates with `team_create` if needed
3. Spawn teammates with appropriate models and turn budgets
4. Teammates claim tasks and work in parallel
5. Monitor via `team_status` and `team_read_inbox`
6. Send follow-up instructions with `team_send` as needed
