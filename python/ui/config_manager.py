#!/usr/bin/env python3
"""
config_manager.py — Interactive GPIOnext configuration tool.

Must be run as root (GPIO access requires it). Stops the GPIOnext daemon
while the tool is active and restarts it on exit.

New features:
  - Live pin test view: full-screen real-time pin monitor with mapping labels
  - Edit existing mappings in-place (navigate, select, re-map)
  - Import / export config as JSON
  - HAT preset loader (Adafruit, Pimoroni, generic NES)
  - Uses gpionext_core.GpioCore.start_monitor() for pin detection
    (no RPi.GPIO dependency)

Usage: gpionext config
       (invoked by /usr/bin/gpionext, runs this file via the venv python)
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

# Ensure both the python/ package directory and install root are on sys.path
# so gpionext_core.so can be imported regardless of the caller's working dir.
_UI_DIR = os.path.dirname(os.path.realpath(__file__))
_PYTHON_DIR = os.path.dirname(_UI_DIR)
_INSTALL_ROOT = os.path.dirname(_PYTHON_DIR)
sys.path.insert(0, _PYTHON_DIR)
sys.path.insert(0, _INSTALL_ROOT)

import config.SQL as SQL
from config.constants import AVAILABLE_PINS, AVAILABLE_PINS_STRING, DEVICE_LIST, BUTTON_LIST, KEY_LIST

try:
    import gpionext_core
    _HAS_CORE = True
except ImportError:
    _HAS_CORE = False
    print('WARNING: gpionext_core.so not found — pin detection will be disabled')

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

_COLORS = {
    'red': '\033[31m', 'green': '\033[32m', 'yellow': '\033[33m',
    'blue': '\033[34m', 'fuschia': '\033[35m', 'cyan': '\033[36m',
    'bold': '\033[1m',  'reset': '\033[0m',
}

def pcolor(color: str, text: str) -> str:
    """
    Wrap text in an ANSI color escape.

    Parameters:
        color (str): color name (red/green/yellow/blue/fuschia/cyan/bold)
        text  (str): text to colorise

    Returns:
        str: ANSI-escaped string
    """
    return f"{_COLORS.get(color.lower(), '')}{text}{_COLORS['reset']}"


# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description='GPIOnext Configuration Manager')
parser.add_argument('--combo_delay', metavar='50', default=50, type=int,
                    help='Combo window in milliseconds')
parser.add_argument('--key_hold_delay', metavar='350', default=350, type=int,
                    help='Key repeat delay in milliseconds')
parser.add_argument('--pins', metavar='3,5,7,11', type=str,
                    default=AVAILABLE_PINS_STRING,
                    help='Comma-delimited BOARD pin numbers to watch')
parser.add_argument('--debounce', metavar='1', default=1, type=int,
                    help='Debounce time in milliseconds')
parser.add_argument('--pulldown', dest='pulldown', default=False, action='store_true',
                    help='Use pulldown resistors instead of pullup')
parser.add_argument('--use_i2c', dest='use_i2c', default=False, action='store_true',
                    help='Enable I2C hardware (MCP23017/ADS1115). Disables GPIO on pins 3 and 5.')
parser.add_argument('--dev', dest='dev', default=False, action='store_true')
parser.add_argument('--debug', dest='debug', default=False, action='store_true')


# ---------------------------------------------------------------------------
# ConfigurationManager
# ---------------------------------------------------------------------------

class ConfigurationManager:
    """
    Interactive configuration tool for GPIOnext.

    Stops the daemon, opens GPIO monitoring via GpioCore.start_monitor(),
    presents menus, and saves config to SQLite. On exit, optionally restarts
    the daemon.
    """

    #: Seconds the user must hold a button for it to register (prevents accidents)
    HOLD_SECONDS = 1.0
    #: Poll interval for wait_for_pin
    POLL_INTERVAL = 0.05

    def __init__(self, args: argparse.Namespace) -> None:
        if os.geteuid() != 0:
            sys.exit(pcolor('red', 'ERROR: config_manager must be run as root'))

        self.args = self._normalise_args(args)
        self._core: 'gpionext_core.GpioCore | None' = None

        self._stop_daemon()

        for sig in (signal.SIGTERM, signal.SIGQUIT, signal.SIGINT):
            signal.signal(sig, self._signal_handler)

        SQL.init()
        self._start_gpio_monitor()
        self._main_loop()

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def _stop_daemon(self) -> None:
        """Stop the systemd service and wait for it to fully exit."""
        subprocess.call(('systemctl', 'stop', 'gpionext'))
        time.sleep(0.5)

    def _start_gpio_monitor(self) -> None:
        """
        Start a lightweight GPIO monitor (no uinput) so wait_for_pin() works.
        Falls back to a no-op stub if gpionext_core is unavailable.
        """
        if not _HAS_CORE:
            return
        self._core = gpionext_core.GpioCore()
        try:
            config_dict = SQL.buildConfigDict(self.args)
            self._core.start_monitor(config_dict)
        except RuntimeError as exc:
            print(pcolor('yellow', f'WARNING: GPIO monitor failed to start: {exc}'))
            print(pcolor('yellow', 'Pin detection will be disabled.'))
            self._core = None

    def _signal_handler(self, sig: int, frame) -> None:
        """Clean exit on SIGTERM/SIGINT/SIGQUIT."""
        os.system('reset')
        if self._core:
            self._core.stop()
        print()
        print(pcolor('cyan', "Type 'gpionext start' to run the daemon"))
        sys.exit(0)

    def _cleanup(self) -> None:
        """Stop GPIO monitor and optionally restart the daemon."""
        if self._core:
            self._core.stop()
        answer = input(pcolor('cyan', '\nStart the GPIOnext daemon? [Y/n] ')).strip().lower()
        if answer in ('', 'y', 'yes'):
            subprocess.call(('systemctl', 'start', 'gpionext'))
            print(pcolor('green', 'gpionext daemon started.'))
        else:
            print(pcolor('cyan', "Type 'gpionext start' to run the daemon"))

    # ---------------------------------------------------------------------------
    # Main menu loop
    # ---------------------------------------------------------------------------

    def _main_loop(self) -> None:
        """Present the top-level menu repeatedly until the user exits."""
        while True:
            choice = self._show_main_menu()

            if choice is None:           # Exit
                self._cleanup()
                sys.exit(0)
            elif choice == 'live_pins':
                self._show_live_pins()
            elif choice == 'hardware_settings':
                self._show_hardware_settings()
            elif choice == 'preset':
                self._load_preset()
            elif choice == 'export':
                self._export_config()
            elif choice == 'import':
                self._import_config()
            elif isinstance(choice, dict):
                device_name = choice['name']
                if device_name == 'Keyboard':
                    self._configure_keyboard(choice)
                elif device_name == 'Commands':
                    self._configure_commands()
                elif device_name.startswith('Joypad'):
                    self._configure_joypad(choice)
                elif device_name == 'Clear Device':
                    self._clear_device()
                elif device_name == 'Edit Existing':
                    self._edit_existing()

    def _show_main_menu(self) -> object:
        """
        Display the top-level menu and return the user's selection.

        Returns:
            dict with 'name' key for device/action, or None for exit,
            or a string key for special actions ('live_pins', 'preset', etc.)
        """
        print()
        print(pcolor('bold', '─' * 50))
        print(pcolor('cyan', '  GPIOnext Configuration Manager'))
        print(pcolor('bold', '─' * 50))

        options = [
            ('1', 'Joypad 1',      {'name': 'Joypad 1'}),
            ('2', 'Joypad 2',      {'name': 'Joypad 2'}),
            ('3', 'Joypad 3',      {'name': 'Joypad 3'}),
            ('4', 'Joypad 4',      {'name': 'Joypad 4'}),
            ('5', 'Keyboard',      {'name': 'Keyboard'}),
            ('6', 'Commands',      {'name': 'Commands'}),
            ('─', '─' * 30,        None),
            ('7', 'Edit existing mapping', 'edit_existing'),
            ('8', 'Clear a device',        'clear_device'),
            ('─', '─' * 30,        None),
            ('9', 'Live pin monitor',      'live_pins'),
            ('h', 'Hardware settings',     'hardware_settings'),
            ('p', 'Load HAT preset',       'preset'),
            ('e', 'Export config (JSON)',  'export'),
            ('i', 'Import config (JSON)',  'import'),
            ('─', '─' * 30,        None),
            ('q', 'Exit',          None),
        ]

        for key, label, _ in options:
            if key == '─':
                print(pcolor('bold', f'  {label}'))
            else:
                print(f'  {pcolor("cyan", key)}) {label}')

        print()
        choice = input('Select: ').strip().lower()

        for key, _, value in options:
            if choice == key and key != '─':
                if key == 'q':
                    return None
                elif isinstance(value, str):
                    return value
                else:
                    return value
        return 'live_pins' if choice == '9' else None

    # ---------------------------------------------------------------------------
    # Pin detection
    # ---------------------------------------------------------------------------

    def wait_for_pin(self) -> list[int]:
        """
        Block until the user holds at least one GPIO pin for HOLD_SECONDS.
        Uses gpionext_core.get_pin_states() to poll the bitmask.
        Returns the list of BOARD pins currently held when the hold threshold
        is reached.

        Returns:
            list[int]: BOARD pin numbers that were held (one or more for combos)
        """
        hold_start: float | None = None
        last_bitmask: int = 0

        while True:
            if _HAS_CORE:
                bitmask = gpionext_core.get_pin_states()
            else:
                bitmask = 0

            time.sleep(self.POLL_INTERVAL)

            if bitmask == 0:
                hold_start = None
                last_bitmask = 0
                continue

            if bitmask != last_bitmask:
                # New press or combo change — reset hold timer
                hold_start = time.time()
                last_bitmask = bitmask
                continue

            if hold_start and (time.time() - hold_start) >= self.HOLD_SECONDS:
                # Convert bitmask to pin list
                return [bit for bit in range(192) if bitmask & (1 << bit)]

    def wait_for_release(self) -> None:
        """Block until all GPIO pins are released. Prompts after 3 seconds."""
        start = time.time()
        prompted = False
        while True:
            if _HAS_CORE:
                bitmask = gpionext_core.get_pin_states()
            else:
                bitmask = 0

            if bitmask == 0:
                return
            time.sleep(self.POLL_INTERVAL)
            if not prompted and (time.time() - start) > 3:
                print(pcolor('cyan', '  Please release all buttons to continue'))
                prompted = True

    # ---------------------------------------------------------------------------
    # Device configuration
    # ---------------------------------------------------------------------------

    def _configure_joypad(self, device_info: dict) -> None:
        """
        Interactively configure axes and buttons for a joypad device.

        Parameters:
            device_info (dict): must contain 'name' key (e.g. 'Joypad 1')
        """
        device_name = device_info['name']
        print(f'\n{pcolor("bold", f"Configuring {device_name}")}')

        axis_count = self._ask_axis_count()
        entries: list[tuple] = []

        # Axes
        for i in range(1, axis_count + 1):
            for direction, (axis_code, value) in (
                ('UP',    (1, -255)),
                ('DOWN',  (1,  255)),
                ('LEFT',  (0, -255)),
                ('RIGHT', (0,  255)),
            ):
                label = pcolor('cyan', f'DPAD {i} {direction}')
                print(f'  Hold pin(s) for {label}: ', end='', flush=True)
                pins = self.wait_for_pin()
                pins_str = self._pins_to_str(pins)
                print(f'→ {pins_str}')
                self.wait_for_release()
                entries.append((device_name, f'DPAD {i} {direction}', 'AXIS',
                                f'(3, {axis_code}, {value})', pins_str))

        # Buttons
        selected_buttons = self._select_buttons_to_configure()
        for btn_name, btn_code in selected_buttons:
            label = pcolor('cyan', btn_name)
            print(f'  Hold pin(s) for {label}: ', end='', flush=True)
            pins = self.wait_for_pin()
            pins_str = self._pins_to_str(pins)
            print(f'→ {pins_str}')
            self.wait_for_release()
            entries.append((device_name, btn_name, 'BUTTON', str(btn_code), pins_str))

        print(pcolor('green', f'  Saving {device_name} configuration…'))
        SQL.deleteDevice(device_name)
        SQL.createDevice(entries)
        print(pcolor('green', '  Done.'))

    def _configure_keyboard(self, device_info: dict) -> None:
        """
        Interactively configure keyboard key mappings.

        Parameters:
            device_info (dict): contains 'name' and 'buttons' (list of (name, code))
        """
        device_name = 'Keyboard'
        print(f'\n{pcolor("bold", "Configuring Keyboard")}')

        selected_keys = self._select_keys_to_configure()
        entries: list[tuple] = []

        for key_name, key_code in selected_keys:
            label = pcolor('cyan', key_name)
            print(f'  Hold pin(s) for {label}: ', end='', flush=True)
            pins = self.wait_for_pin()
            pins_str = self._pins_to_str(pins)
            print(f'→ {pins_str}')
            self.wait_for_release()
            entries.append((device_name, key_name, 'KEY', str(key_code), pins_str))

        print(pcolor('green', '  Saving Keyboard configuration…'))
        SQL.deleteDevice(device_name)
        SQL.createDevice(entries)
        print(pcolor('green', '  Done.'))

    def _configure_commands(self) -> None:
        """Add, edit, or delete custom GPIO command mappings."""
        while True:
            print(f'\n{pcolor("bold", "Commands")}')
            rows = SQL.getDeviceRaw('Commands')

            for i, row in enumerate(rows, 1):
                print(f'  {pcolor("cyan", str(i))}. [{row["pins"]}] {row["name"]}: {row["command"]}')

            print(f'\n  {pcolor("cyan", "a")}. Add new command')
            print(f'  {pcolor("cyan", "d")}. Delete a command')
            print(f'  {pcolor("cyan", "b")}. Back to main menu')

            choice = input('\n  Select: ').strip().lower()

            if choice == 'b':
                break
            elif choice == 'a':
                self._add_command()
            elif choice == 'd' and rows:
                idx = input('  Delete entry #: ').strip()
                try:
                    row = rows[int(idx) - 1]
                    SQL.deleteEntry(row)
                    print(pcolor('green', '  Deleted.'))
                except (ValueError, IndexError):
                    print(pcolor('red', '  Invalid selection.'))
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(rows):
                    self._edit_command(rows[idx])

    def _add_command(self) -> None:
        """Prompt for a new command name, shell command, and pin assignment."""
        name = input('  Command name: ').strip()
        if not name:
            return
        cmd = input('  Shell command (separate multiple with ;): ').strip()
        if not cmd:
            return
        print(f'  Hold pin(s) to trigger {pcolor("cyan", name)}: ', end='', flush=True)
        pins = self.wait_for_pin()
        pins_str = self._pins_to_str(pins)
        print(f'→ {pins_str}')
        self.wait_for_release()
        SQL.updateEntry({'id': None, 'device': 'Commands', 'name': name,
                         'type': 'COMMAND', 'command': cmd, 'pins': pins_str})
        print(pcolor('green', '  Command added.'))

    def _edit_command(self, row: dict) -> None:
        """Edit an existing command row (name, command string, or pin)."""
        print(f'\n  Editing: {row["name"]}')
        name = input(f'  Name [{row["name"]}]: ').strip() or row['name']
        cmd = input(f'  Command [{row["command"]}]: ').strip() or row['command']
        repin = input('  Re-assign pin? [y/N]: ').strip().lower()
        pins_str = row['pins']
        if repin == 'y':
            print('  Hold new pin(s): ', end='', flush=True)
            pins = self.wait_for_pin()
            pins_str = self._pins_to_str(pins)
            print(f'→ {pins_str}')
            self.wait_for_release()
        SQL.updateEntry({'id': row['id'], 'device': 'Commands', 'name': name,
                         'type': 'COMMAND', 'command': cmd, 'pins': pins_str})
        print(pcolor('green', '  Updated.'))

    # ---------------------------------------------------------------------------
    # Edit existing mappings
    # ---------------------------------------------------------------------------

    def _edit_existing(self) -> None:
        """Browse all DB mappings and allow the user to edit or delete them."""
        rows = SQL.getAllRows()
        if not rows:
            print(pcolor('yellow', '  No mappings configured yet.'))
            return

        while True:
            print(f'\n{pcolor("bold", "Edit Existing Mappings")}')
            for i, row in enumerate(rows, 1):
                print(f'  {pcolor("cyan", str(i)):>4}. [{row["device"]:10}] '
                      f'{row["name"]:20} pins={row["pins"]}')
            print(f'\n  {pcolor("cyan", "b")}. Back')

            choice = input('\n  Select entry # to edit (or b): ').strip().lower()
            if choice == 'b':
                break
            try:
                row = rows[int(choice) - 1]
            except (ValueError, IndexError):
                print(pcolor('red', '  Invalid selection.'))
                continue

            print(f'\n  Editing: [{row["device"]}] {row["name"]} (pins={row["pins"]})')
            action = input('  (r)e-assign pin  (d)elete  (b)ack: ').strip().lower()

            if action == 'd':
                SQL.deleteEntry(row)
                rows = SQL.getAllRows()
                print(pcolor('green', '  Deleted.'))
            elif action == 'r':
                print(f'  Hold new pin(s) for {pcolor("cyan", row["name"])}: ',
                      end='', flush=True)
                pins = self.wait_for_pin()
                pins_str = self._pins_to_str(pins)
                print(f'→ {pins_str}')
                self.wait_for_release()
                row['pins'] = pins_str
                SQL.updateEntry(row)
                rows = SQL.getAllRows()
                print(pcolor('green', '  Updated.'))

    # ---------------------------------------------------------------------------
    # Clear device
    # ---------------------------------------------------------------------------

    def _clear_device(self) -> None:
        """Remove all mappings for a selected device."""
        print(f'\n{pcolor("bold", "Clear Device")}')
        for i, name in enumerate(DEVICE_LIST, 1):
            print(f'  {pcolor("cyan", str(i))}. {name}')
        print(f'  {pcolor("cyan", "b")}. Back')

        choice = input('\n  Select: ').strip().lower()
        if choice == 'b':
            return
        try:
            name = DEVICE_LIST[int(choice) - 1]
        except (ValueError, IndexError):
            return
        confirm = input(f'  Delete ALL mappings for {pcolor("red", name)}? [y/N]: ').strip().lower()
        if confirm == 'y':
            SQL.deleteDevice(name)
            print(pcolor('green', f'  {name} cleared.'))

    def _show_hardware_settings(self) -> None:
        """Menu for I2C baudrate and chip management."""
        import config.baudrate as baudrate
        while True:
            print(f'\n{pcolor("bold", "Hardware Settings")}')
            
            use_i2c = getattr(self.args, 'use_i2c', False)
            if not use_i2c:
                print(pcolor('yellow', '  [!] I2C is currently DISABLED.'))
                print(pcolor('yellow', '      Run "gpionext set use_i2c true" to enable.'))
                print()

            print(f'  {pcolor("cyan", "1")}. I2C Baudrate (Current: {baudrate.get_current_baudrate()} Hz)')
            print(f'  {pcolor("cyan", "2")}. Manage MCP23017 chips')
            print(f'  {pcolor("cyan", "3")}. Manage ADS1115 chips')
            print(f'  {pcolor("cyan", "b")}. Back')

            choice = input('\n  Select: ').strip().lower()
            if choice == 'b':
                break
            elif choice == '1':
                self._configure_baudrate()
            elif choice == '2':
                self._manage_mcp23017()
            elif choice == '3':
                self._manage_ads1115()

    def _configure_baudrate(self) -> None:
        import config.baudrate as baudrate
        print(f'\n{pcolor("bold", "Configure I2C Baudrate")}')
        print(baudrate.ADVANCED_WARNING)
        print(f'\n  {pcolor("cyan", "1")}. Default (100,000 Hz)')
        print(f'  {pcolor("cyan", "2")}. Fast    (400,000 Hz)')
        print(f'  {pcolor("cyan", "b")}. Back')

        choice = input('\n  Select: ').strip().lower()
        if choice == '1':
            if baudrate.set_baudrate(100000):
                print(pcolor('green', '  Baudrate set to 100kHz. Reboot required.'))
        elif choice == '2':
            if baudrate.set_baudrate(400000):
                print(pcolor('green', '  Baudrate set to 400kHz. Reboot required.'))

    def _manage_mcp23017(self) -> None:
        while True:
            print(f'\n{pcolor("bold", "Manage MCP23017 Chips")}')
            rows = SQL._cursor.execute('SELECT * FROM I2C_MCP23017').fetchall()
            for i, row in enumerate(rows, 1):
                int_pin = row['int_pin'] if row['int_pin'] else "None"
                print(f'  {pcolor("cyan", str(i))}. Bus {row["bus"]}, Addr 0x{row["address"]:02X}, Int Pin: {int_pin}')
            
            print(f'\n  {pcolor("cyan", "a")}. Add new chip')
            print(f'  {pcolor("cyan", "d")}. Delete a chip')
            print(f'  {pcolor("cyan", "b")}. Back')

            choice = input('\n  Select: ').strip().lower()
            if choice == 'b':
                break
            elif choice == 'a':
                try:
                    bus_str = input('  I2C Bus [1]: ').strip() or '1'
                    bus = int(bus_str)
                    addr_str = input('  I2C Address (hex) [0x20]: ').strip() or '0x20'
                    addr = int(addr_str, 16)
                    int_pin_str = input('  Interrupt Pin (BOARD) [None]: ').strip()
                    int_pin = int(int_pin_str) if int_pin_str else None
                    SQL._cursor.execute('INSERT INTO I2C_MCP23017 (bus, address, int_pin) VALUES (?,?,?)', (bus, addr, int_pin))
                    SQL._conn.commit()
                    print(pcolor('green', '  Chip added.'))
                except ValueError:
                    print(pcolor('red', '  Invalid input.'))
            elif choice == 'd' and rows:
                try:
                    idx = int(input('  Delete chip #: ').strip())
                    row = rows[idx - 1]
                    SQL._cursor.execute('DELETE FROM I2C_MCP23017 WHERE id = ?', (row['id'],))
                    SQL._conn.commit()
                    print(pcolor('green', '  Deleted.'))
                except (ValueError, IndexError):
                    print(pcolor('red', '  Invalid selection.'))

    def _manage_ads1115(self) -> None:
        while True:
            print(f'\n{pcolor("bold", "Manage ADS1115 Chips")}')
            rows = SQL._cursor.execute('SELECT * FROM I2C_ADS1115').fetchall()
            for i, row in enumerate(rows, 1):
                print(f'  {pcolor("cyan", str(i))}. Bus {row["bus"]}, Addr 0x{row["address"]:02X}')
            
            print(f'\n  {pcolor("cyan", "a")}. Add new chip')
            print(f'  {pcolor("cyan", "d")}. Delete a chip')
            print(f'  {pcolor("cyan", "b")}. Back')

            choice = input('\n  Select: ').strip().lower()
            if choice == 'b':
                break
            elif choice == 'a':
                try:
                    bus_str = input('  I2C Bus [1]: ').strip() or '1'
                    bus = int(bus_str)
                    addr_str = input('  I2C Address (hex) [0x48]: ').strip() or '0x48'
                    addr = int(addr_str, 16)
                    SQL._cursor.execute('INSERT INTO I2C_ADS1115 (bus, address) VALUES (?,?)', (bus, addr))
                    SQL._conn.commit()
                    print(pcolor('green', '  Chip added.'))
                except ValueError:
                    print(pcolor('red', '  Invalid input.'))
            elif choice == 'd' and rows:
                try:
                    idx = int(input('  Delete chip #: ').strip())
                    row = rows[idx - 1]
                    SQL._cursor.execute('DELETE FROM I2C_ADS1115 WHERE id = ?', (row['id'],))
                    SQL._conn.commit()
                    print(pcolor('green', '  Deleted.'))
                except (ValueError, IndexError):
                    print(pcolor('red', '  Invalid selection.'))

    # ---------------------------------------------------------------------------
    # Live pin monitor
    # ---------------------------------------------------------------------------

    def _show_live_pins(self) -> None:
        """Launch the full-screen live pin monitor. Includes I2C pins if configured."""
        from ui.live_pin_view import LivePinView
        db_rows = SQL.getAllRows()
        
        # Start with physical BOARD pins
        pins_to_show = list(self.args.pins)
        
        # Add configured I2C pins only if enabled
        if getattr(self.args, 'use_i2c', False):
            mcp_chips = SQL._cursor.execute('SELECT address FROM I2C_MCP23017').fetchall()
            for mcp in mcp_chips:
                addr = mcp['address']
                base_vpin = 64 + (addr - 0x20) * 16
                pins_to_show.extend(range(base_vpin, base_vpin + 16))
                
            ads_chips = SQL._cursor.execute('SELECT address FROM I2C_ADS1115').fetchall()
            for ads in ads_chips:
                addr = ads['address']
                base_vpin = 128 + (addr - 0x48) * 4
                pins_to_show.extend(range(base_vpin, base_vpin + 4))

        with LivePinView(pins_to_show, db_rows) as view:
            view.run()

    # ---------------------------------------------------------------------------
    # HAT preset loader
    # ---------------------------------------------------------------------------

    def _load_preset(self) -> None:
        """Let the user pick a HAT preset and apply it to the database."""
        from ui.hat_presets import get_preset_names, get_display_name, preset_to_db_rows

        keys = get_preset_names()
        print(f'\n{pcolor("bold", "Load HAT Preset")}')
        for i, key in enumerate(keys, 1):
            print(f'  {pcolor("cyan", str(i))}. {get_display_name(key)}')
        print(f'  {pcolor("cyan", "b")}. Back')

        choice = input('\n  Select: ').strip().lower()
        if choice == 'b':
            return
        try:
            key = keys[int(choice) - 1]
        except (ValueError, IndexError):
            print(pcolor('red', '  Invalid selection.'))
            return

        rows = preset_to_db_rows(key)
        if not rows:
            print(pcolor('red', '  Preset is empty or invalid.'))
            return

        print(f'\n  Preset "{get_display_name(key)}" will create {len(rows)} mapping(s).')
        confirm = input('  Overwrite existing mappings for affected devices? [y/N]: ').strip().lower()
        if confirm != 'y':
            return

        # Group by device name and replace each device
        devices_affected = {r[0] for r in rows}
        for device_name in devices_affected:
            SQL.deleteDevice(device_name)
        SQL.createDevice(rows)
        print(pcolor('green', f'  Preset "{get_display_name(key)}" applied.'))

    # ---------------------------------------------------------------------------
    # Import / Export
    # ---------------------------------------------------------------------------

    def _export_config(self) -> None:
        """Export the full config database to a JSON file."""
        default_path = '/opt/gpionext/config_backup.json'
        path = input(f'  Export path [{default_path}]: ').strip() or default_path
        try:
            data = SQL.exportToJson()
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            print(pcolor('green', f'  Exported {len(data)} rows to {path}'))
        except OSError as exc:
            print(pcolor('red', f'  Export failed: {exc}'))

    def _import_config(self) -> None:
        """Import a config from a JSON file, replacing the current database."""
        path = input('  Import file path: ').strip()
        if not os.path.isfile(path):
            print(pcolor('red', f'  File not found: {path}'))
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(pcolor('red', f'  Failed to read file: {exc}'))
            return

        confirm = input(f'  Replace current config with {len(data)} entries from {path}? [y/N]: ').strip().lower()
        if confirm != 'y':
            return
        SQL.importFromJson(data, replace=True)
        print(pcolor('green', f'  Imported {len(data)} entries.'))

    # ---------------------------------------------------------------------------
    # Selection helpers
    # ---------------------------------------------------------------------------

    def _ask_axis_count(self) -> int:
        """
        Ask how many D-pads/joystick axes the joypad device should have.

        Returns:
            int: number of axes (1-4)
        """
        print()
        for i in range(1, 5):
            print(f'  {pcolor("cyan", str(i))}. {i} D-pad{"s" if i > 1 else ""} / joystick{"s" if i > 1 else ""}')
        try:
            return max(1, min(4, int(input('\n  How many D-pads/joysticks? [1]: ').strip() or '1')))
        except ValueError:
            return 1

    def _select_buttons_to_configure(self) -> list[tuple[str, int]]:
        """
        Show BUTTON_LIST and let the user pick which buttons to configure.
        Returns the selected (name, evdev_code) pairs.
        """
        print(f'\n{pcolor("bold", "Select buttons to configure:")}')
        for i, (name, _) in enumerate(BUTTON_LIST, 1):
            print(f'  {pcolor("cyan", str(i)):>5}. {name}')
        print(f'\n  Enter numbers separated by commas, or "all" for all buttons:')
        raw = input('  Selection: ').strip().lower()
        if raw == 'all':
            return list(BUTTON_LIST)
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(',')]
            return [BUTTON_LIST[i] for i in indices if 0 <= i < len(BUTTON_LIST)]
        except (ValueError, IndexError):
            return []

    def _select_keys_to_configure(self) -> list[tuple[str, int]]:
        """
        Show KEY_LIST and let the user pick which keys to configure.
        Returns the selected (name, evdev_code) pairs.
        """
        print(f'\n{pcolor("bold", "Select keys to configure:")}')
        for i, (name, _) in enumerate(KEY_LIST, 1):
            print(f'  {pcolor("cyan", str(i)):>5}. {name}')
        print(f'\n  Enter numbers separated by commas, or "all":')
        raw = input('  Selection: ').strip().lower()
        if raw == 'all':
            return list(KEY_LIST)
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(',')]
            return [KEY_LIST[i] for i in indices if 0 <= i < len(KEY_LIST)]
        except (ValueError, IndexError):
            return []

    # ---------------------------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------------------------

    def _normalise_args(self, args: argparse.Namespace) -> argparse.Namespace:
        """Parse pin string into a list of ints."""
        args.pins = [int(x.strip()) for x in args.pins.split(',') if x.strip()]
        return args

    def _pins_to_str(self, pins: list[int]) -> str:
        """
        Convert a pin list to the canonical DB storage format.
        Now supports virtual I2C pins.
        """
        out = []
        for p in pins:
            if p >= 128:
                # ADS1115
                addr = 0x48 + (p - 128) // 4
                ch = (p - 128) % 4
                out.append(f"i2c-0x{addr:02X}-ch{ch}")
            elif p >= 64:
                # MCP23017
                addr = 0x20 + (p - 64) // 16
                port = 'A' if ((p - 64) % 16) < 8 else 'B'
                bit = (p - 64) % 8
                out.append(f"i2c-0x{addr:02X}-{port}{bit}")
            else:
                out.append(str(p))
        
        if len(out) == 1:
            return out[0]
        return str(tuple(out))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    args = parser.parse_args()
    ConfigurationManager(args)
