# Project Log

## Status
Active — The live pin monitor now reuses the configuration menu's existing curses screen/lifecycle so the parent menu can resume cleanly without nested curses.wrapper() calls.

## Last Updated
2026-05-15

## Current Architecture
- `python/ui/config_manager.py` provides the interactive GPIOnext configuration UI.
- The UI is built around the vendored `cursesmenu` package (`CursesMenu`, `SelectionMenu`, and `MultiSelect`) for menu navigation.
- GPIO pin detection flows through `gpionext_core` when available, with `ConfigurationManager.wait_for_pin()` polling pin states and `wait_for_release()` blocking until release.
- GPIO capture UI is centralized through `_capture_pins()`, which now treats an empty pin list as cancellation/unavailability and lets callers abort cleanly before database writes.
- Configuration data is persisted through `config.SQL`, including device mappings, command mappings, I2C chip configuration, and JSON import/export.
- Stored DB `pins` values are parsed through shared `config.SQL` helpers so single pins, tuple/list combos, and virtual I2C strings are handled without `eval`.
- Full-screen live monitoring is delegated to `python/ui/live_pin_view.py`; the main config manager owns menu workflows and database writes.

## Completed Milestones
- [x] Initialized this project log as the long-term memory/source of truth for the current session.
- [x] Centralized confirmation prompts in `ConfigurationManager._confirm()` using `SelectionMenu`.
- [x] Centralized action selection in `ConfigurationManager._choose_action()`.
- [x] Centralized free-text prompting in `ConfigurationManager._text_input()` so curses mode is suspended/restored in one location.
- [x] Centralized GPIO pin-capture status display in curses-compatible helpers instead of inline pin-capture `print()` calls.
- [x] Updated nested `config_manager.py` menu callbacks to pass the immediate active submenu as the parent when launching child selection menus.
- [x] Added `ConfigurationManager._show_message()` for one-button, curses-safe status messages.
- [x] Replaced SQL mutation success/error `print()` feedback in config manager workflows with `_show_message()`.
- [x] Ensured SQL-backed command, mapping, MCP23017, and ADS1115 management loops return to their rebuild loops after mutating actions so fresh rows are fetched.
- [x] Replaced the no-mappings `time.sleep(1)` feedback path in `_edit_existing()` with a menu-safe `_show_message()` prompt.
- [x] Corrected `CursesMenu.selected_item` to return `items[selected_option]` while preserving the existing no-selection guard, improving callers that compare selected items against `exit_item`.
- [x] Normalized `MultiSelect` button/key selections in `config_manager.py`, treating `None`, `[]`, and `[-1]` as cancellation and comparing valid item labels via `set[str]`.
- [x] Added shared safe pin parsing/formatting helpers in `config.SQL` and replaced DB pin `eval()` parsing in runtime config and live pin labels.
- [x] Updated menu display paths to format stored physical and virtual pins consistently via the shared parser.
- [x] Added monitor availability checks to `wait_for_pin()` and `wait_for_release()` so GPIO polling is skipped when `_HAS_CORE` is false or no core instance is active.
- [x] Added a 30-second default GPIO capture timeout with retry/cancel handling in `wait_for_pin()`.
- [x] Updated joypad, keyboard, command, and existing-mapping edit workflows to abort without saving when pin capture returns no pins.
- [x] Updated the live pin monitor to expose `LivePinView.run_in_window(stdscr)` for drawing into an existing curses screen while keeping `run()` as the standalone/manual `curses.wrapper()` entry point.
- [x] Routed the configuration menu's live pin monitor through a shared fullscreen curses helper that restores blocking input state, clears/refreshes the parent screen, and avoids nested wrappers when returning to the menu.

## Known Issues & Lessons Learned
- Nested curses selection menus should receive the immediate active submenu as `parent`; passing the grandparent can break return/redraw behavior when exiting child menus.
- SQL-backed menus should exit child selections after mutating actions and re-enter their outer loops so displayed rows are fetched from the database again.
- `python -m py_compile` creates `__pycache__` files; remove those generated artifacts before committing.
- The repository currently has an untracked `core/Cargo.lock` that was not created by this task and should not be committed unless intentionally requested.
- Direct terminal `input()` calls conflict with curses menu rendering; future text prompts should use `_text_input()` rather than adding new raw prompts.
- Direct yes/no `input()` prompts drift from menu UX; future confirmations should use `_confirm()`.
- Direct `print()` calls are poor UI feedback while curses menus are active; future status/success/error feedback in menu workflows should prefer `_show_message()` or `_show_status()` depending on whether acknowledgement is needed.
- `selected_item` must reflect `selected_option`, not the currently highlighted `current_option`; callers may compare `menu.selected_item == menu.exit_item` after selection handling changes the highlighted row.
- `MultiSelect.get_selection()` returns selected item labels for successful selections, but may return sentinel cancellation values (`None`, `[]`, or `[-1]`); normalize those sentinels before converting labels to a comparison set.
- Stored DB `pins` values may be plain integers, tuple/list strings, quoted numeric strings, or virtual I2C identifiers; future parsing should use `SQL.parse_pins_value()`/`SQL.pin_value_to_vpin()` rather than `eval()`.
- GPIO capture callers must treat an empty `wait_for_pin()` result as cancellation/unavailability and skip database writes to avoid saving empty or invalid pin mappings.
- Full-screen tools launched from the curses menu should reuse `CursesMenu.stdscr` via `LivePinView.run_in_window()`/`_run_fullscreen_curses()`; starting a nested `curses.wrapper()` before returning to an existing menu can corrupt terminal/menu state.
- Full-screen curses callbacks that set nonblocking input must restore blocking timeout/keypad state and explicitly clear/refresh the parent screen before handing control back to `cursesmenu`.
