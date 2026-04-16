---
name: git
description: >-
  Git safety rules, workflow helpers for commits, branching, and pull requests.
  Use when working with git operations or version control.
tags: [git, vcs]
---

# Git Safety Rules

## Absolute prohibitions (unless user explicitly requests)

- NEVER update the git config
- NEVER run destructive/irreversible commands (push --force, hard reset, etc.)
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc.)
- NEVER force push to main/master — warn the user if they request it
- NEVER commit changes unless the user explicitly asks

## Amend rules

Avoid `git commit --amend`. ONLY use --amend when ALL conditions are met:

1. User explicitly requested amend, OR commit SUCCEEDED but pre-commit hook auto-modified files that need including
2. HEAD commit was created by you in this conversation (verify: `git log -1 --format='%an %ae'`)
3. Commit has NOT been pushed to remote (verify: `git status` shows "Your branch is ahead")

**CRITICAL:**
- If commit FAILED or was REJECTED by hook, NEVER amend — fix the issue and create a NEW commit
- If you already pushed to remote, NEVER amend unless user explicitly requests it (requires force push)

# Git Workflow

## Commit messages

- Use imperative mood in the subject line ("Add feature" not "Added feature")
- Limit the subject to 50 characters; wrap the body at 72
- Separate subject from body with a blank line
- Use the body to explain *what* and *why*, not *how*

## Branching

- Branch from `main` for features: `feature/<short-name>`
- Branch from `main` for fixes: `fix/<short-name>`
- Delete branches after merge

## Pull requests

- Keep PRs focused on a single concern
- Write a summary describing the change and motivation
- Reference related issues with `Closes #<n>` or `Fixes #<n>`
- Ensure CI passes before requesting review

## Pre-commit checklist

1. Run the test suite: `pytest`
2. Run the linter: `ruff check .`
3. Run the formatter: `ruff format --check .`
4. Confirm no secrets or credentials are staged
