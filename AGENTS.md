# Agent Instructions

This repository contains the Imou Life HACS custom integration (`custom_components/imou_life`).

## Before opening a PR

1. Run `script/setup` once in a fresh environment.
2. Run `script/lint` and `script/test` — both must pass.
3. Use `.github/PULL_REQUEST_TEMPLATE.md` — do not remove unchecked checklist items.
4. Do not amend or squash commits after a PR has received review.

## Code constraints

- Keep `domain=imou_life` and existing `unique_id` / device key semantics unless explicitly scoped.
- Tests must use public Home Assistant APIs (config flow, services). Do not call coordinator internals directly.
- Match `pyimouapi` version in `manifest.json` when changing dependencies.

## AI limitations

AI-generated suggestions do not replace maintainer review. Contributors are responsible for understanding every line submitted.
