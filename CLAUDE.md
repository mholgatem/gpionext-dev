# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**GPIOnext** maps Raspberry Pi GPIO pins to virtual HID devices (joypads, keyboards) and shell commands, primarily for game console emulators (RetroPie, PiPlay). It runs as a systemd daemon and creates virtual input devices via Linux `uinput`/`evdev`.

## Refactoring Context

**All new and reused code lives in `./refactor/`** — the existing code at the repo root is reference only. Do not modify the original files in-place.

### Refactoring Goals
- **Lower latency** in GPIO event processing and HID event dispatch
- **Broader compatibility**: Raspberry Pi 2B and beyond (including Pi 5/Bookworm)
- **i2c support** (currently only avoided; must be properly handled)
- **Multi-language**: Python, C/C++, shell, and potentially Rust are all on the table
- **pip install fix**: Modern Debian/Raspberry Pi OS requires `--break-system-packages` or a venv — install script must handle this without breaking the system package manager
- **Improved CLI**: The `usr-bin-gpionext` bash wrapper and `config_manager.py` curses UI should both be enhanced for usability
- **Install/update/remove scripts** must be included in `./refactor/`

### Languages In Use
- **Python 3** — daemon, config manager, GPIO event handling
- **Bash** — install/update/remove scripts, CLI wrapper (`usr-bin-gpionext`)
- **SQLite** — configuration persistence via `config.db`
- Additional languages (C, Rust, etc.) may be introduced for latency-critical paths

## Code Standards (Critical — Read Before Writing Any Function)

1. **Always check for existing helper functions first.** Before writing a new function, search the codebase. If a helper already does the same or nearly the same thing, modify it to handle the new case rather than creating a duplicate. Backward compatibility with existing call sites must be preserved.
2. **Documentation blocks are required before every function**: describe parameters, return values, and what the function is expected to do.
3. **Inline comments**: explain non-obvious logic; don't over-document simple operations.

## Architecture (Reference Code)

### Core Data Flow
```
GPIO pin interrupt → bitmask update → device pressEvents() queue
→ combo_delay timer → processQueue() bitmask match → AbstractEvent.press()
→ uinput EV_KEY / EV_ABS write → HID device
```

### Key Components

| File | Role |
|---|---|
| `gpionext.py` | Daemon entry point; CLI args, signal handling, main loop |
| `config/device.py` | `Device` class — creates uinput virtual devices, owns peripheral list |
| `config/gpio.py` | GPIO setup (BOARD mode), event detection, global bitmask |
| `config/SQL.py` | SQLite CRUD for device/button mappings |
| `config/constants.py` | Pin lists by Pi model, device list, button/key evdev mappings |
| `config/menus.py` | Application-level curses menu flows |
| `config_manager.py` | Interactive configuration tool (requires root) |
| `cursesmenu/` | Generic curses menu framework (CursesMenu, SelectionMenu, MultiSelect) |
| `usr-bin-gpionext` | Bash CLI wrapper installed to `/usr/bin/gpionext` |
| `install.sh` | Installs dependencies, udev rules, systemd service |
| `update.sh` | Git pull + service restart |
| `remove.sh` | Full uninstall |

### Virtual Device Types
- **Joypad 1–4**: uinput gamepad, vendor `9999` / product `8888`, up to 24 buttons + axes
- **Keyboard**: uinput keyboard device named "GPIOnext Keyboard"
- **Commands**: no device; runs shell commands via `subprocess`

### Event Class Hierarchy
```
AbstractEvent
├── Axis    — EV_ABS joystick analog (range -255 to +255, flat 15)
├── Button  — EV_KEY joystick button press/release
├── Key     — EV_KEY keyboard key with hold repeat (after 350 ms)
└── Command — subprocess.call() with bash
```

### Combo / Multi-Button Logic
- All pressed pins are combined into a bitmask
- `combo_delay` (default 50 ms) allows multi-button windows
- Queue processor sorts candidates by pin count; longest match wins
- `bitmaskIn()` on each AbstractEvent determines eligibility

### GPIO Pin Handling
- Physical BOARD numbering
- I2C pins 3 and 5 must not be set to pulldown (currently skipped with a warning)
- `AVAILABLE_PINS` in `constants.py` varies by Pi model

### Configuration Storage
SQLite table `GPIOnext`:
```
id | device (Joypad 1..4, Keyboard, Commands) | name | type (AXIS/BUTTON/KEY/COMMAND) | command | pins
```
Database lives at `config/config.db`; default path `/home/pi/gpionext/config/config.db` on device.

### Systemd Service
- Unit: `gpionext.service`
- Start: `python3 -u gpionext.py --debounce 1 --combo_delay 50`
- **SIGHUP** triggers hot-reload (re-reads DB, reinitializes GPIO)
- **SIGTERM/SIGINT/SIGQUIT** trigger graceful shutdown

### CLI Commands (`gpionext <cmd>`)
```
start | stop | reload | config | disable | journal
set combo_delay <ms>
set debounce <ms>
set pins <list|default>
set pulldown <true|false>
set dev <true|false>
set debug <true|false>
test <1-4>
```

## Installation Notes

### pip on Modern Raspberry Pi OS
Debian Bookworm+ enforces externally-managed Python environments. The install script must use one of:
- `pip install --break-system-packages <pkg>` (quick, not recommended long-term)
- A project-scoped virtualenv activated by the service and CLI wrapper
- `apt install python3-<pkg>` where distro packages are available (preferred for `evdev`, `RPi.GPIO`)

### GPIO Library Compatibility
- Pi 1–4 / Bullseye: `RPi.GPIO`
- Pi 5 / Bookworm: `rpi-lgpio` (drop-in RPi.GPIO replacement)
- Install script must detect Debian version and install the correct library

### udev Rule (SDL2 compatibility)
```
KERNEL=="event*", ATTRS{idVendor}=="9999", ATTRS{idProduct}=="8888", MODE:="0644"
```
Required for SDL2 applications (emulators) to see the virtual gamepads.

## Refactor Directory Layout (Current)

```
refactor/
├── .github/workflows/build.yml   # Cross-compile Rust → armv7l/aarch64/x86_64
├── install.sh                    # Arch detect, binary download, venv, udev, systemd
├── update.sh                     # Git pull, refresh binary, restart service
├── remove.sh                     # Full uninstall
├── usr-bin-gpionext              # Bash CLI wrapper → /usr/bin/gpionext
├── gpionext.service              # Systemd unit (venv Python)
├── core/                         # Rust crate → gpionext_core.so (PyO3)
│   ├── Cargo.toml
│   ├── pyproject.toml
│   └── src/
│       ├── lib.rs        GpioCore class + parse_peripherals() exposed to Python
│       ├── bitmask.rs    AtomicU64 bitmask, Rayon pool, combo resolution (bug fix)
│       ├── gpio.rs       libgpiod event loop (feature-gated: --features gpio)
│       ├── i2c.rs        MCP23017 + ADS1115 + IoPin trait (feature-gated: --features i2c)
│       └── uinput.rs     Virtual HID writes + key hold loop (no new threads)
└── python/
    ├── gpionext.py               # Daemon: HAT detect → config dict → GpioCore
    ├── config/
    │   ├── constants.py          # Pin lists, i2c pin ID helpers
    │   ├── SQL.py                # SQLite CRUD + I2C tables + JSON import/export
    │   ├── hat_detect.py         # Detect audio HAT from /boot/config.txt + EEPROM
    │   └── baudrate.py           # Configure RPi I2C baudrate (100kHz / 400kHz)
    └── ui/
        ├── config_manager.py     # Interactive config tool (I2C/Hardware menu)
        ├── live_pin_view.py      # Curses full-screen 192-bit real-time pin monitor
        ├── hat_presets.py        # Adafruit/Pimoroni/NES preset pin maps
        └── cursesmenu/           # Carried over from reference (unmodified)
```

## Implementation Status

- [x] **Phase 1**: Core Scaffolding (Rust extension + Python wrapper)
- [x] **Phase 2**: GPIO and uinput (gpiocdev loop + libc writes)
- [x] **Phase 3**: I2C Support (MCP23017, ADS1115, IRQ pins, Baudrate)
- [ ] **Final Verification**: Live testing on Raspberry Pi hardware

