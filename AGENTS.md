# Repository Guidelines

## Project Structure & Module Organization
- `auto_click.py` — main automation entry point. Window detection, OpenCV template matching, and click logic live here; per-server/per-account options are tuned in the user-config block near the top of the file.
- `make_tower_templates.py`, `make_tpjj_templates.py` — regenerate match templates when the game UI changes.
- `selftest_tpjj.py` — standalone self-test for template matching quality and coverage.
- `probe_multi.py` — inspect windows/regions, useful for multi-monitor setups.
- `diag_msg_backend.py` and `debug_shots/diag/` — diagnostic messaging and captured screenshots for debugging matches.
- `templates/` — runtime asset directory with match PNGs; deploy it next to the scripts/binary.
- Python 3 with `opencv-python` and `numpy`; `pywin32` is required and enables background (后台) mode.

## Build, Test, and Development Commands
- `python auto_click.py` — run the automation locally (edit the config block first).
- `python selftest_tpjj.py` — run template self-tests after touching templates or thresholds.
- `python make_tpjj_templates.py` / `python make_tower_templates.py` — regenerate templates after UI changes.
- `python probe_multi.py` — verify window/region coordinates before changing targeting code.
- Install dependencies with `pip install opencv-python numpy pywin32`.
- No linter or formatter is configured; keep changes consistent with the existing style.

## Coding Style & Naming Conventions
- Use 4-space indentation and follow PEP 8. Module-level constants use `UPPER_SNAKE_CASE`; functions use `snake_case`.
- Template PNGs use lowercase snake_case names (e.g. `tz_button.png`, `tpjj_start.png`).
- Declare thresholds and ROIs as named constants (e.g. `THRESHOLD`, `POPUP_REGION`), never as inline magic numbers.
- Comments and log messages are written in Chinese; preserve UTF-8 encoding when editing.

## Testing Guidelines
- There is no pytest-based suite; `selftest_tpjj.py` is the current self-test harness. Run it before finishing template-related changes.
- Image matching requires a real game window. After changing thresholds or ROIs, verify on an actual window and save diagnostics under `debug_shots/diag/` when sharing results.

## Commit & Pull Request Guidelines
- This repo has no Git history or version control yet, so no existing convention applies. If Git is introduced, use short, verb-first commit messages stating what changed and why.
- For any change description: state the behavioral change, the account/screen setup used to verify, and attach relevant screenshots or diagnostics from `debug_shots/`.

## Security & Configuration Tips
- Never commit real account credentials or personal window titles; keep the top-of-file config block to generic defaults.
- `templates/` paths are relative, so always deploy it alongside the script or packaged binary.
- Change match thresholds conservatively and validate with the self-test before committing.
