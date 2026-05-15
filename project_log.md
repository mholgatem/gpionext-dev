# Project Log

## Status
Active — UI configuration manager cleanup in progress.

## Last Updated
2026-05-15

## Current Architecture
- `python/ui/config_manager.py` provides the interactive GPIOnext configuration UI.
- The UI is built around the vendored `cursesmenu` package (`CursesMenu`, `SelectionMenu`, and `MultiSelect`) for menu navigation.
- GPIO pin detection flows through `gpionext_core` when available, with `ConfigurationManager.wait_for_pin()` polling pin states and `wait_for_release()` blocking until release.
- Configuration data is persisted through `config.SQL`, including device mappings, command mappings, I2C chip configuration, and JSON import/export.
- Full-screen live monitoring is delegated to `python/ui/live_pin_view.py`; the main config manager owns menu workflows and database writes.

## Completed Milestones
- [x] Initialized this project log as the long-term memory/source of truth for the current session.
- [x] Centralized confirmation prompts in `ConfigurationManager._confirm()` using `SelectionMenu`.
- [x] Centralized action selection in `ConfigurationManager._choose_action()`.
- [x] Centralized free-text prompting in `ConfigurationManager._text_input()` so curses mode is suspended/restored in one location.
- [x] Centralized GPIO pin-capture status display in curses-compatible helpers instead of inline pin-capture `print()` calls.

## Known Issues & Lessons Learned
- `python -m py_compile` creates `__pycache__` files; remove those generated artifacts before committing.
- The repository currently has an untracked `core/Cargo.lock` that was not created by this task and should not be committed unless intentionally requested.
- Direct terminal `input()` calls conflict with curses menu rendering; future text prompts should use `_text_input()` rather than adding new raw prompts.
- Direct yes/no `input()` prompts drift from menu UX; future confirmations should use `_confirm()`.
