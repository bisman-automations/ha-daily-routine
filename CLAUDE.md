# Daily Routine: notes for Claude

Home Assistant custom integration (`daily_routine`) for scheduled household routines. Installed through HACS. Read this before changing anything.

## Layout

- `custom_components/daily_routine/`
  - `coordinator.py`: scheduling, safety checks, execution, notifications, storage, people and notify helpers.
  - `routines.py`: validation and normalisation of routine data (`normalize_routine`). Every stored routine goes through here.
  - `const.py`: trigger names, storage key, limits.
  - `schema.py`: the only place that imports the schema library. Always `from .schema import vol`, never `voluptuous` or `probatio` directly.
  - `panel.py`: sidebar websocket API (`daily_routine/...` commands) and the overview payload.
  - `devices.py`: hub device and one service device per routine.
  - `sensor.py`, `switch.py`: per-routine status sensors and the pause switch.
  - `frontend/daily-routine-panel.js`: the whole sidebar UI, one file, no build step.
- `tests/`: pytest (Python backend) and `test_panel_frontend.mjs` (Node tests of the panel with a stubbed `HTMLElement`).
- `README.md` is user documentation. `CHANGELOG.md` is the release history.

## Checks to run before saying work is done

Run from the repo root, using a venv with `requirements_test.txt` installed:

```
ruff check custom_components tests
ruff format --check custom_components tests
mypy custom_components/daily_routine
pytest
node --test tests/test_panel_frontend.mjs
```

CI also runs hassfest, the HACS action, and the same tests against an older Home Assistant pin (`pytest-homeassistant-custom-component==0.13.367`). Keep code compatible with both.

## Conventions

- Routine data is validated in `routines.py`. Add new fields there first, then the panel draft mapping (`_draftFromRoutine` / `_draftToRaw`), then tests.
- Safety rules are fail-closed. Unknown or unavailable people and sensors block a run, and conditions are re-checked before each step. Don't weaken these without asking.
- Value triggers (`value_above` / `value_below`) fire on a crossing, not on every reading. Turn-off levels create a companion routine named `<id>_off` through `_companion_for`.
- Panel tests stub the DOM. When you change a template or an editor field, add or update a Node test.
- Keep UI wording plain. Show names, never entity ids, in the panel. Actions come from the `ACTIONS` list in the panel, each with a plain label. Add new actions there, and keep "Other (advanced)" for anything not listed.

## Releases

- Version lives in `custom_components/daily_routine/manifest.json` and `pyproject.toml`. Bump both together.
- Every push to `main` creates or refreshes a draft GitHub release for the manifest version. Publishing that draft creates the tag. Tag pushes from this sandbox are refused, so don't try to push tags. Ask the user to publish the draft.
- Add an entry under `## [Unreleased]` in `CHANGELOG.md` with each change, and rename it to the version when releasing.
- Commit and push only when the user asks.

## Privacy rule for repo docs

Never put account-specific details in the README, CHANGELOG, code, or tests: no account numbers, service ids, real device names, addresses, or the user's own entity ids. Use placeholders such as `sensor.bathroom_humidity` or `notify.mobile_app_phone`.

## Open decisions (ask before acting)

- The repo has no LICENSE file, so the HACS action fails. The user has to choose the license.
- Open decisions: none. The license is still open. Ask the user before adding a LICENSE file.
