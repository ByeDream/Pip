# Pip-Boy TUI Themes

This directory is **your themes sandbox**. On first boot pip-boy
seeds a few example themes here (e.g. `wasteland/`, `vault-amber/`).
After that it's yours — edit, add, or delete freely. The scaffold
respects deletions: once you remove a seeded theme it stays gone
across reboots.

```
.pip/themes/<slug>/
    theme.toml      # required: manifest (name, palette, widget toggles)
    theme.tcss      # required: Textual CSS
    art.txt         # optional: ASCII art (≤ 32 cols × 8 rows)
```

Slug rules: lowercase letters, digits, and dashes; must start with a
letter; must match the directory name (`name = "<slug>"` in
`theme.toml`).

## CLI

* `/theme list` — show installed themes; active marked with `*`.
* `/theme set <slug>` — switch to the theme **immediately** (hot
  reload — no restart) and persist the choice to
  `.pip/host_state.json` so next boot comes up in the same theme.
* `/theme refresh` — re-scan this directory. Use it after dropping
  a new theme directory in or editing a `theme.toml` manually; the
  command reports added / removed / broken themes.

Authoring guide: see `docs/themes.md`.
