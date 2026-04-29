# GPIOnext

A high-performance GPIO-to-HID daemon for Raspberry Pi, designed for arcade and retro-gaming setups. It maps GPIO pin events (buttons, joysticks, combos) to virtual keyboard keys, joysticks, or system commands.

## Project Structure

This repository is a refactored version of the original GPIOnext.
- **`core/` (Rust):** Performance-critical extension (GPIO event loop, combo engine, uinput writes).
- **`python/` (Python):** Daemon wrapper, configuration management, and UI.
- **`Legacy/`:** The original Python-only implementation, kept for reference.

## Architecture & Data Flow

1. **GPIO Event Loop (Rust):** Monitors pins via `gpiocdev`.
2. **Bitmask Engine (Rust):** Updates a global bitmask of pressed pins.
3. **Combo Resolution (Rust):** Uses a `combo_delay` (default 50ms) to resolve multi-pin combos.
4. **Event Dispatch (Rust):** Triggers events based on the resolved bitmask.
5. **HID Writes (Rust):** Uses `uinput` to simulate keystrokes, gamepad buttons, or axes.
6. **Command Execution (Python/Rust):** Runs shell commands for "COMMAND" type peripherals.

### Event Hierarchy
- **Button:** EV_KEY joystick button press/release.
- **Key:** EV_KEY keyboard key with auto-repeat (350ms delay).
- **Axis:** EV_ABS joystick analog movement.
- **Command:** Shell command execution via bash.

## Key Technologies

- **Rust:** `gpiocdev` (GPIO), `rayon` (parallelism), `pyo3` (bindings), `parking_lot`.
- **Python:** `argparse`, `sqlite3` (config storage), `curses` (UI).
- **Deployment:** `systemd` service, `udev` rules for SDL2/emulator compatibility.

## Building and Development

### Rust Core
```bash
cd core
cargo build --release
```
Copy/symlink `target/release/libgpionext_core.so` to the project root as `gpionext_core.so`.

### Python Wrapper
Main entry: `python/gpionext.py`.
UI tool: `python/ui/config_manager.py`.

### Rust Core Features
The Rust core uses feature flags to manage dependencies and functionality:
- `gpio` (Default: Off): Enables the `gpiocdev` event loop.
- `i2c` (Default: Off): Enables MCP23017 and ADS1115 drivers.

Build with features using: `cargo build --release --features "gpio i2c"`

## Configuration
- **Database:** `config/config.db` (SQLite).
- **CLI Flags:** See `gpionext --help` or `python/gpionext.py`.
- **Hot-Reload:** `SIGHUP` signal triggers a configuration reload without restarting the daemon.

## Development Conventions
- **Legacy Compatibility:** Maintain the existing SQLite schema and CLI interface.
- **Performance:** Keep the hot-path (GPIO → HID) in Rust.
- **GPIO Pins:** Use physical BOARD numbering.
- **I2C:** Reserved pins (3, 5) should be handled with care (detect audio HATs).
- **Validation:** Always test changes with the live pin view (`python/ui/live_pin_view.py`) and the config tool.
