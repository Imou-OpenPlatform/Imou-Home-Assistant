# Contributing to Imou Home Assistant

Thank you for contributing to the Imou Life HACS integration. This guide explains how to set up your environment, run checks locally, and open a pull request.

## Prerequisites

- Python 3.12 or newer
- [git](https://git-scm.com/)
- [uv](https://github.com/astral-sh/uv) (installed automatically by `script/setup` if missing)

## Getting started

1. Fork this repository on GitHub.
2. Clone your fork and create a feature branch from `main`:

   ```bash
   git checkout -b feat/my-change main
   ```

3. Install development dependencies:

   ```bash
   script/setup
   ```

## Development workflow

1. Make your changes under `custom_components/imou_life/` and/or `tests/`.
2. Format and lint:

   ```bash
   script/lint
   ```

3. Run tests:

   ```bash
   script/test
   ```

4. Optional: run the same checks CI uses without modifying files:

   ```bash
   script/lint-check
   ```

Pre-commit hooks run automatically on `git commit` after `script/setup`.

### Suggested branch names

- `fix/…` for bug fixes
- `feat/…` for new features
- `chore/…` for tooling or documentation

## Code standards

- Python code is formatted and linted with [Ruff](https://docs.astral.sh/ruff/).
- Follow [Home Assistant integration patterns](https://developers.home-assistant.io/docs/creating_component_index/) where applicable.
- Preserve backward compatibility:
  - `domain` must remain `imou_life`
  - Do not change existing entity `unique_id` or device key rules without an explicit migration plan
- Keep `pyimouapi` version in `manifest.json` aligned with `pyproject.toml` dev dependencies when bumping dependencies.

## Dependency upgrades

[Dependabot](https://docs.github.com/en/code-security/dependabot) opens weekly PRs for **GitHub Actions** only. Python dependencies are upgraded manually so `pyproject.toml`, `uv.lock`, and (when needed) `manifest.json` stay in sync.

### Dev-only packages (`ruff`, `pytest`, `homeassistant`, etc.)

1. Bump the version in `pyproject.toml` (`[dependency-groups].dev`).
2. Regenerate the lockfile: `uv lock`
3. Run `script/lint-check` and `script/test`.
4. Open a `chore/…` PR.

### `pyimouapi` (runtime + dev)

This package is installed for end users via `manifest.json` and for local/CI testing via `pyproject.toml`. Update **all three** in one PR:

1. `pyproject.toml` — `[dependency-groups].dev`
2. `custom_components/imou_life/manifest.json` — `requirements`
3. `uv lock`
4. Run `script/lint-check` and `script/test`; manually verify against Imou devices if the API changed.
5. Open a `chore/…` PR. CI **Manifest** must pass (versions must match).

## Testing

- Every PR must include **manual verification** in a real Home Assistant instance (or dev container) for the behavior you changed. Describe the steps and results in the PR **Testing** section.
- Automated tests live in `tests/` and use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component).
- Use public Home Assistant APIs in tests (config flow, services, entity states).
- Do **not** call coordinator internals such as `coordinator.async_request_refresh()` directly.
- Mark tests that need custom component loading with the `enable_custom_integrations` fixture.

## Opening a pull request

1. Push your branch to your fork.
2. Open a PR targeting **`main`**.
3. Fill out `.github/PULL_REQUEST_TEMPLATE.md` completely.
4. Ensure all CI checks pass:
   - **Lint**, **Spell**, **YAML**, **Hassfest**, **HACS**, **Manifest**, **Test**
5. If you used AI tools, check the AI boxes in the PR template.

### Review process

1. CODEOWNERS are automatically requested for review.
2. A maintainer reviews functionality, compatibility, and test coverage.
3. Merge requires **one approval** and **green CI**.
4. Maintainers squash-merge to `main`.

### PR labels (maintainers)

| Label | Use |
|-------|-----|
| `bug` | Bug fix |
| `enhancement` | New feature |
| `breaking-change` | Breaking user-facing change |
| `needs-tests` | Missing or insufficient tests |
| `ci-failure` | CI needs contributor attention |

## Release process (maintainers)

1. Update `CHANGELOG.md` and bump `version` in `custom_components/imou_life/manifest.json`.
2. Merge changes to `main`.
3. Tag on `main`: `git tag vX.Y.Z && git push origin vX.Y.Z`
4. The release workflow publishes the HACS zip asset.

## Branch protection (maintainers)

Configure in GitHub → **Settings** → **Branches** → rule for `main`:

- Require a pull request before merging (1 approval recommended)
- Require status checks: **Lint**, **Spell**, **YAML**, **Hassfest**, **HACS**, **Manifest**, **Test**
- Dismiss stale approvals when new commits are pushed

**HACS** runs only on the canonical repository `Imou-OpenPlatform/Imou-Home-Assistant`; fork PRs may skip it.

## Appendix: 中文快速指引

1. **环境**：`script/setup` 安装依赖与 pre-commit。
2. **提交前**：`script/lint` + `script/test` 必须通过。
3. **PR 目标分支**：`main`；使用仓库 PR 模板填写说明。
4. **测试约定**：不要直接调用 coordinator 内部方法；需要加载集成时使用 `enable_custom_integrations` fixture。
5. **依赖升级**：仅 GitHub Actions 由 Dependabot 自动提 PR；Python 依赖手动升级。升 `pyimouapi` 须同时改 `pyproject.toml`、`manifest.json` 并执行 `uv lock`。
6. **更多细节**：见上文英文章节。
