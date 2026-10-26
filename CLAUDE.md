# Daily Routine: notes for Claude and other coding agents

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

## Checks

Run the shared checks at the end of this file, plus:

```
mypy custom_components/daily_routine
node --test tests/test_panel_frontend.mjs
```

CI runs both (mypy on the newest Home Assistant). The integration depends on
`frontend`, so the tests need `home-assistant-frontend`; CI installs it with
the `core-requirements: frontend` input, and locally `pip install
home-assistant-frontend==<the version in
homeassistant/components/frontend/manifest.json>`.

## Conventions

- Routine data is validated in `routines.py`. Add new fields there first, then the panel draft mapping (`_draftFromRoutine` / `_draftToRaw`), then tests.
- Safety rules are fail-closed. Unknown or unavailable people and sensors block a run, and conditions are re-checked before each step. Don't weaken these without asking.
- Value triggers (`value_above` / `value_below`) fire on a crossing, not on every reading. Turn-off levels create a companion routine named `<id>_off` through `_companion_for`.
- Panel tests stub the DOM. When you change a template or an editor field, add or update a Node test.
- Keep UI wording plain. Show names, never entity ids, in the panel. Actions come from the `ACTIONS` list in the panel, each with a plain label. Add new actions there, and keep "Other (advanced)" for anything not listed.

## Open decisions (ask before acting)

- None. The license is MIT (chosen by the user on 2026-10-18).

<!-- BEGIN bisman-automations shared rules -->
<!-- This block is the same in every bisman-automations repo. Edit it in
     bisman-automations/.github (templates/CLAUDE.shared.md) and copy it to
     every repo; the Validate workflow's "standards" job checks it matches. -->

## Shared rules (every bisman-automations repo)

### Releases

- The version lives in `custom_components/<domain>/manifest.json`. Anything
  else that repeats it is called out in the repo-specific notes below and is
  bumped in the same commit.
- Versions follow [Semantic Versioning](https://semver.org): patch for fixes,
  translations, docs and CI; minor for new features, options or entities;
  major when something users rely on is removed or renamed (entities,
  services, events, options) or setup has to be redone.
- Check what is released before choosing a version:
  `git ls-remote --tags origin | awk -F/ '{print $3}' | sort -V | tail -1`
  (`sort -V`: plain sorting puts `v1.10.0` before `v1.9.0`). Never add
  changes to a released version. If the manifest version has no tag yet,
  add to its CHANGELOG section instead of bumping again.
- Every push to `main` ships as a release. In the same push: bump the
  manifest version, and add a dated `## [X.Y.Z] - YYYY-MM-DD` section to
  `CHANGELOG.md` (Keep a Changelog: Added, Changed, Deprecated, Removed,
  Fixed, Security) with its compare link at the bottom. Small follow-ups can
  collect under `## [Unreleased]` first; move them into the version section
  when you bump.
- Ask Donavan before every push. Push fast-forward only: never force-push,
  rebase or amend anything already pushed, or rewrite history in any other
  way. Rewriting history orphans the release tags (it broke drafting in
  ha-button-actions).
- Pushing to `main` runs **Draft release**, which creates or refreshes a
  draft `vX.Y.Z` with that version's CHANGELOG section as the notes, the
  commits since the last release and `<domain>.zip`. **Donavan publishes
  the draft himself**, and publishing creates the tag. Never create or push
  tags, publish releases, or edit published releases.
- Publishing runs **Release check**: the tag must match the manifest version
  and the CHANGELOG, and be on `main`. If it fails, fix it with a new patch
  release, not by moving the tag.

### Commits

- Author commits as `Donavan Becker <donavan.becker@icloud.com>` (set
  `git config user.name` / `user.email` in the clone). HACS's
  default-repository check needs the submitter to have a fair share of the
  commits. Credit an AI assistant with a `Co-Authored-By:` trailer if you
  like, never as the author.
- Subject lines are plain, imperative and say what changed for the user
  ("Show Pico remotes as unavailable when Lutron isn't running"). No
  `fix:`/`feat:` prefixes. Release commits are "Release X.Y.Z".
- Commit and push only when asked.

### CI (shared workflows)

- `.github/workflows/` holds four thin callers of the reusable workflows in
  [bisman-automations/.github](https://github.com/bisman-automations/.github):
  `test.yml` (lint, frontend, pytest on the oldest supported and the newest
  Home Assistant, plus the upcoming one as information only), `validate.yml`
  (hassfest, HACS, standards), `draft-release.yml` and `release-check.yml`.
  Don't add jobs to the callers. Use the shared workflow's inputs, or change
  the shared workflow so every repo gets the improvement.
- `requirements_test.txt` leaves `pytest-homeassistant-custom-component`
  unpinned (newest Home Assistant) and pins ruff (and mypy where used).
  `requirements_test_min.txt` pins the harness release for the minimum in
  `hacs.json`: the newest harness release of that month. CI fails if they
  drift apart.
- Raising the minimum Home Assistant means changing, together: `hacs.json`,
  `requirements_test_min.txt`, `python-min` in `test.yml` (3.13 up to
  2026.2, 3.14 from 2026.3), the README's Home Assistant badge, and a
  CHANGELOG entry.
- Home Assistant 2026.3 and newer need Python 3.14 (3.14.2+).
- The published hassfest rejects `categories` in `manifest.json` and Pillow
  in `requirements`. Leave both out.
- Dependabot (`.github/dependabot.yml`) proposes action and pinned-tool
  updates weekly. It ignores the test harness on purpose.
- Test and Validate also run every Thursday, the day after Home Assistant's
  betas come out. The **next** test job (newest pre-release harness) is
  information only: when it fails, fix the cause before that release ships.

### Before saying work is done

Run from the repo root, in a Python 3.14 venv with `requirements_test.txt`
installed:

```
ruff check .
ruff format --check .
pytest
```

Also run `node --test tests/*.mjs` when the repo has Node tests, mypy when
the repo-specific notes list it, and the tests in a second venv with
`requirements_test_min.txt` when a change could behave differently on the
oldest supported Home Assistant. The standards check (also in CI) is
`python <path to bisman-automations/.github>/scripts/check_repo.py .`.

### Code and docs

- Support every Home Assistant from the `hacs.json` minimum to the newest
  release. Check for deprecations in the newest release and the beta.
- Keep entity IDs, unique IDs, service names and event names stable. When
  one has to change, migrate it and say so under Changed in the CHANGELOG.
- `strings.json` and `translations/en.json` hold the same keys. Home
  Assistant reads only `translations/`, so change both in the same commit.
- Never put personal details in the repo (README, CHANGELOG, code, tests,
  screenshots): no names, addresses, account or device identifiers, or real
  entity IDs. Use placeholders such as `light.living_room`.
- `README.md` is user documentation with the standard header (badges, HACS
  and "add integration" My buttons) and sections Installation, Setup, ...,
  Troubleshooting, Development, License. Use plain language and show names,
  not entity IDs, wherever the UI does.
<!-- END bisman-automations shared rules -->
