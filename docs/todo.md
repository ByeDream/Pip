# Pip-Boy Agent -- Backlog

## Skills

- [ ] **babysit**: PR 看护 skill -- 循环检查 PR 状态/评论/CI，自动修复直到可合并（需要先有 GitHub API 工具）
- [ ] **create-subagent**: 自定义子代理配置 -- 允许用户定义专用子代理（code-reviewer, debugger 等），各自有独立 system prompt

## Tools

- [ ] **GitHub API 工具**: PR 操作、issue 管理、CI 状态查询（babysit skill 的前置依赖）
- [ ] **grep/ripgrep 工具**: 专用代码搜索工具，比 bash + grep 更结构化

## Agent Team

- [ ] **Lead-as-teammate 统一模型**: 将 lead 和 teammate 视为同一种角色，消除当前的双轨机制。

  **现状问题**:
  - 工具分两套: `LEAD_TOOLS` + `TEAMMATE_EXTRA_TOOLS`，teammate 有 6 个专属工具（send/read_inbox/idle/claim_task/task_board_overview/task_board_detail），lead 通过 `TEAMMATE_BLOCKED_TOOLS` 黑名单排除 teammate 工具
  - 任务管理双轨: lead 用 `task_update` 设 in_progress/owner，teammate 用 `claim_task`。测试中 lead 越权 pre-assign owner 给 teammate，teammate 不看 task board
  - `_handle_claim_task` 在 `tool_dispatch.py` 中检查 `ctx.teammate is None` 则拒绝，lead 无法 claim
  - Teammate 有独立的 `_build_tools()` 拼装逻辑（lead 工具过滤黑名单 + extra tools）

  **目标设计**:
  - 统一工具池: 一份工具列表，lead 和 teammate 都用。`claim_task`、`task_board_overview`、`task_board_detail` 对 lead 也可用。取消 `TEAMMATE_EXTRA_TOOLS` 和 `TEAMMATE_BLOCKED_TOOLS` 概念
  - 统一任务认领: 所有人（lead/teammate）通过 `claim_task` 开始任务，`claim_task` 自动设 owner 为调用者。`task_update` 不再允许设 `status: in_progress`（或仅作 fallback）
  - Lead solo = team size 1: lead 自己 `claim_task` -> 工作 -> 标 completed，和 teammate 的流程完全一致
  - Teammate 专属行为（send/read_inbox/idle）改为条件可用: 这些工具在 lead 上下文中隐藏（lead 有自己的 team_send/team_read_inbox），在 teammate 上下文中可见。区分方式从"两套工具列表"变为"一套列表 + 角色 filter"
  - `_build_tools()` 简化: 不再需要黑名单拼装，用角色 tag 过滤即可

  **关键改动点**:
  - `tools.py`: 合并 LEAD_TOOLS + TEAMMATE_EXTRA_TOOLS 为统一 ALL_TOOLS，每个 schema 加 `"roles"` 元数据标记适用角色
  - `tool_dispatch.py`: `_handle_claim_task` 去掉 teammate-only 限制，lead 调用时 owner 设为 "lead"
  - `team/__init__.py`: `_build_tools()` 改为按角色过滤统一列表
  - `task_graph.py`: 考虑 `task_update` 是否限制 in_progress 状态变更
  - `agent-team skill` / `task-planning skill`: 描述统一的 claim 流程，不再区分 solo/team 模式
- [ ] **Non-interactive long-running lead**: 将 Pip 改为非交互式长运行 agent，不再依赖用户 nudge 来推进 teammate 完成后的流程。

## Infrastructure

- [ ] **Persistent memory**: 跨会话记忆存储（项目上下文、用户偏好）
- [ ] **Configurable persona**: 可配置人格/语气
- [ ] **Skill hot-reload**: 运行时检测 skill 文件变更，无需重启
