---
name: git
description: >-
  Git workflow helpers for commits, branching, and pull requests.
  Use when working with git operations or version control.
tags: [git, vcs]
---

# Git workflow helpers

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
