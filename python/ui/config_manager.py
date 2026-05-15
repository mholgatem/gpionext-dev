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
import curses
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
sys.path.insert(0, os.path.join(_UI_DIR, 'cursesmenu'))

import cursesmenu
from cursesmenu import CursesMenu, SelectionMenu, MultiSelect
from cursesmenu.items import FunctionItem, SubmenuItem, MenuItem

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
                    help='Combo window in milliseconds (inherited from daemon)')
parser.add_argument('--key_hold_delay', metavar='350', default=350, type=int,
                    help='Key repeat delay in milliseconds (inherited from daemon)')
parser.add_argument('--pins', metavar='3,5,7,11', type=str,
                    default=AVAILABLE_PINS_STRING,
                    help='Comma-delimited BOARD pin numbers to watch (inherited from daemon)')
parser.add_argument('--debounce', metavar='1', default=1, type=int,
                    help='Debounce time in milliseconds (inherited from daemon)')
parser.add_argument('--pulldown', dest='pulldown', default=False, action='store_true',
                    help='Use pulldown resistors instead of pullup (inherited from daemon)')
parser.add_argument('--use_i2c', dest='use_i2c', default=False, action='store_true',
                    help='Enable I2C hardware (MCP23017/ADS1115). (inherited from daemon)')
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
        if self._confirm('GPIOnext daemon', 'Start the GPIOnext daemon?', default_no=False):
            subprocess.call(('systemctl', 'start', 'gpionext'))
            print(pcolor('green', 'gpionext daemon started.'))
        else:
            print(pcolor('cyan', "Type 'gpionext start' to run the daemon"))

    # ---------------------------------------------------------------------------
    # Main menu loop
    # ---------------------------------------------------------------------------

    def _main_loop(self) -> None:
        """Present the top-level menu using cursesmenu."""
        menu = CursesMenu("GPIOnext", "Configuration Manager")

        # Joypads
        for i in range(1, 5):
            menu.append_item(FunctionItem(f"Joypad {i}", self._configure_joypad, [{"name": f"Joypad {i}"}, menu]))
        
        menu.append_item(FunctionItem("Keyboard", self._configure_keyboard, [{"name": "Keyboard"}, menu]))
        menu.append_item(FunctionItem("Commands", self._configure_commands, [menu]))
        
        menu.append_item(FunctionItem("Edit existing mapping", self._edit_existing, [menu]))
        menu.append_item(FunctionItem("Clear a device", self._clear_device, [menu]))
        
        menu.append_item(FunctionItem("Live pin monitor", self._show_live_pins))
        menu.append_item(FunctionItem("Hardware settings", self._show_hardware_settings, [menu]))
        menu.append_item(FunctionItem("Load HAT preset", self._load_preset, [menu]))
        menu.append_item(FunctionItem("Export config (JSON)", self._export_config))
        menu.append_item(FunctionItem("Import config (JSON)", self._import_config, [menu]))

        menu.show()
        self._cleanup()
        sys.exit(0)

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
                self._show_status('Release buttons', 'Please release all buttons to continue')
                prompted = True

    # ---------------------------------------------------------------------------
    # Device configuration
    # ---------------------------------------------------------------------------

    def _configure_joypad(self, device_info: dict, parent: CursesMenu = None) -> None:
        """
        Interactively configure axes and buttons for a joypad device.

        Parameters:
            device_info (dict): must contain 'name' key (e.g. 'Joypad 1')
            parent (CursesMenu): the parent menu
        """
        device_name = device_info['name']

        # 1. Ask for axis count and button selection before starting pin configuration
        axis_count = self._ask_axis_count(parent=parent)
        selected_buttons = self._select_buttons_to_configure(parent=parent)

        print(f'\n{pcolor("bold", f"Configuring {device_name}")}')
        entries: list[tuple] = []

        # Axes
        for i in range(1, axis_count + 1):
            for direction, (axis_code, value) in (
                ('UP',    (1, -255)),
                ('DOWN',  (1,  255)),
                ('LEFT',  (0, -255)),
                ('RIGHT', (0,  255)),
            ):
                label = f'DPAD {i} {direction}'
                pins_str = self._capture_pins(label)
                entries.append((device_name, f'DPAD {i} {direction}', 'AXIS',
                                f'(3, {axis_code}, {value})', pins_str))

        # Buttons
        for btn_name, btn_code in selected_buttons:
            pins_str = self._capture_pins(btn_name)
            entries.append((device_name, btn_name, 'BUTTON', str(btn_code), pins_str))

        print(pcolor('green', f'  Saving {device_name} configuration…'))
        SQL.deleteDevice(device_name)
        SQL.createDevice(entries)
        self._show_message('Joypad configuration', f'{device_name} configuration saved.', parent=parent)

    def _configure_keyboard(self, device_info: dict, parent: CursesMenu = None) -> None:
        """
        Interactively configure keyboard key mappings.

        Parameters:
            device_info (dict): contains 'name' and 'buttons' (list of (name, code))
            parent (CursesMenu): the parent menu
        """
        device_name = 'Keyboard'
        print(f'\n{pcolor("bold", "Configuring Keyboard")}')

        selected_keys = self._select_keys_to_configure(parent=parent)
        entries: list[tuple] = []

        for key_name, key_code in selected_keys:
            pins_str = self._capture_pins(key_name)
            entries.append((device_name, key_name, 'KEY', str(key_code), pins_str))

        print(pcolor('green', '  Saving Keyboard configuration…'))
        SQL.deleteDevice(device_name)
        SQL.createDevice(entries)
        self._show_message('Keyboard configuration', 'Keyboard configuration saved.', parent=parent)

    def _configure_commands(self, parent: CursesMenu = None) -> None:
        """Add, edit, or delete custom GPIO command mappings."""
        while True:
            menu = CursesMenu("Commands", "Manage custom GPIO command mappings")
            rows = SQL.getDeviceRaw('Commands')
            
            for row in rows:
                label = f"[{row['pins']}] {row['name']}: {row['command']}"
                menu.append_item(FunctionItem(label, self._edit_command, [row, menu], should_exit=True))
            
            menu.append_item(FunctionItem("Add new command", self._add_command, [menu], should_exit=True))
            if rows:
                menu.append_item(FunctionItem("Delete a command", self._delete_command_menu, [menu], should_exit=True))
            
            menu.show(parent=parent)
            if menu.selected_option == -1 or menu.selected_item == menu.exit_item:
                break

    def _delete_command_menu(self, parent: CursesMenu = None) -> None:
        """Sub-menu to select a command for deletion."""
        rows = SQL.getDeviceRaw('Commands')
        options = [f"[{row['pins']}] {row['name']}: {row['command']}" for row in rows]
        selection = SelectionMenu.get_selection(options, "Select command to delete", parent=parent)
        if selection != -1:
            if self._confirm('Delete command', f'Delete {rows[selection]["name"]}?', parent=parent):
                SQL.deleteEntry(rows[selection])
                self._show_message('Delete command', 'Command deleted.', parent=parent)

    def _add_command(self, parent: CursesMenu = None) -> None:
        """Prompt for a new command name, shell command, and pin assignment."""
        name = self._text_input('Add command', 'Command name:', parent=parent)
        if not name:
            return
        cmd = self._text_input('Add command', 'Shell command (separate multiple with ;):', parent=parent)
        if not cmd:
            return
        pins_str = self._capture_pins(f'trigger {name}')
        SQL.updateEntry({'id': None, 'device': 'Commands', 'name': name,
                         'type': 'COMMAND', 'command': cmd, 'pins': pins_str})
        self._show_message('Add command', 'Command added.', parent=parent)

    def _edit_command(self, row: dict, parent: CursesMenu = None) -> None:
        """Edit an existing command row (name, command string, or pin)."""
        name = self._text_input('Edit command', 'Name:', default=row['name'], parent=parent)
        if name is None:
            return
        name = name or row['name']
        cmd = self._text_input('Edit command', 'Command:', default=row['command'], parent=parent)
        if cmd is None:
            return
        cmd = cmd or row['command']
        pins_str = row['pins']
        if self._confirm('Edit command', 'Re-assign pin?', parent=parent):
            pins_str = self._capture_pins('new command pin(s)')
        SQL.updateEntry({'id': row['id'], 'device': 'Commands', 'name': name,
                         'type': 'COMMAND', 'command': cmd, 'pins': pins_str})
        self._show_message('Edit command', 'Command updated.', parent=parent)

    # ---------------------------------------------------------------------------
    # Edit existing mappings
    # ---------------------------------------------------------------------------

    def _edit_existing(self, parent: CursesMenu = None) -> None:
        """Browse all DB mappings and allow the user to edit or delete them."""
        while True:
            rows = SQL.getAllRows()
            if not rows:
                self._show_message('Edit Existing Mappings', 'No mappings configured yet.', parent=parent)
                return

            options = [f"[{row['device']:10}] {row['name']:20} pins={row['pins']}" for row in rows]
            selection = SelectionMenu.get_selection(options, "Edit Existing Mappings", parent=parent)
            
            if selection == -1:
                break
                
            row = rows[selection]
            action = self._choose_action(
                f'Editing: [{row["device"]}] {row["name"]} (pins={row["pins"]})',
                ['Re-assign pin', 'Delete', 'Back'],
                parent=parent,
            )

            if action == 1:
                SQL.deleteEntry(row)
                self._show_message('Edit Existing Mappings', 'Mapping deleted.', parent=parent)
                continue
            elif action == 0:
                pins_str = self._capture_pins(row['name'])
                row['pins'] = pins_str
                SQL.updateEntry(row)
                self._show_message('Edit Existing Mappings', 'Mapping updated.', parent=parent)
                continue

    # ---------------------------------------------------------------------------
    # Clear device
    # ---------------------------------------------------------------------------

    def _clear_device(self, parent: CursesMenu = None) -> None:
        """Remove all mappings for a selected device."""
        selection = SelectionMenu.get_selection(DEVICE_LIST, "Select device to clear", parent=parent)
        if selection == -1:
            return
        name = DEVICE_LIST[selection]
        if self._confirm('Clear device', f'Delete ALL mappings for {name}?', parent=parent):
            SQL.deleteDevice(name)
            self._show_message('Clear device', f'{name} cleared.', parent=parent)

    def _show_hardware_settings(self, parent: CursesMenu = None) -> None:
        """Menu for I2C baudrate and chip management."""
        import config.baudrate as baudrate
        while True:
            menu = CursesMenu("Hardware Settings", "I2C and chip management")
            
            use_i2c = getattr(self.args, 'use_i2c', False)
            if not use_i2c:
                menu.subtitle = "I2C is DISABLED (use 'gpionext set use_i2c true')"

            menu.append_item(FunctionItem(f"I2C Baudrate (Current: {baudrate.get_current_baudrate()} Hz)", 
                                         self._configure_baudrate, [menu], should_exit=True))
            menu.append_item(FunctionItem("Manage MCP23017 chips", self._manage_mcp23017, [menu], should_exit=True))
            menu.append_item(FunctionItem("Manage ADS1115 chips", self._manage_ads1115, [menu], should_exit=True))
            
            menu.show(parent=parent)
            if menu.selected_option == -1 or menu.selected_item == menu.exit_item:
                break

    def _configure_baudrate(self, parent: CursesMenu = None) -> None:
        import config.baudrate as baudrate
        options = ["Default (100,000 Hz)", "Fast (400,000 Hz)"]
        selection = SelectionMenu.get_selection(options, "Configure I2C Baudrate", baudrate.ADVANCED_WARNING, parent=parent)
        if selection == 0:
            if baudrate.set_baudrate(100000):
                print(pcolor('green', '  Baudrate set to 100kHz. Reboot required.'))
        elif selection == 1:
            if baudrate.set_baudrate(400000):
                print(pcolor('green', '  Baudrate set to 400kHz. Reboot required.'))

    def _manage_mcp23017(self, parent: CursesMenu = None) -> None:
        while True:
            menu = CursesMenu("Manage MCP23017 Chips")
            rows = SQL._cursor.execute('SELECT * FROM I2C_MCP23017').fetchall()
            for row in rows:
                int_pin = row['int_pin'] if row['int_pin'] else "None"
                label = f"Bus {row['bus']}, Addr 0x{row['address']:02X}, Int Pin: {int_pin}"
                menu.append_item(MenuItem(label)) # Just info for now, maybe add edit later

            menu.append_item(FunctionItem("Add new chip", self._add_mcp23017, [menu], should_exit=True))
            if rows:
                menu.append_item(FunctionItem("Delete a chip", self._delete_mcp23017_menu, [menu], should_exit=True))
            
            menu.show(parent=parent)
            if menu.selected_option == -1 or menu.selected_item == menu.exit_item:
                break

    def _add_mcp23017(self, parent: CursesMenu = None) -> None:
        try:
            bus_str = self._text_input('Add MCP23017', 'I2C Bus:', default='1', parent=parent) or '1'
            bus = int(bus_str)
            addr_str = self._text_input('Add MCP23017', 'I2C Address (hex):', default='0x20', parent=parent) or '0x20'
            addr = int(addr_str, 16)
            int_pin_str = self._text_input('Add MCP23017', 'Interrupt Pin (BOARD):', parent=parent) or ''
            int_pin = int(int_pin_str) if int_pin_str else None
            SQL._cursor.execute('INSERT INTO I2C_MCP23017 (bus, address, int_pin) VALUES (?,?,?)', (bus, addr, int_pin))
            SQL._conn.commit()
            self._show_message('Add MCP23017', 'Chip added.', parent=parent)
        except ValueError:
            self._show_message('Add MCP23017', 'Invalid input.', parent=parent)

    def _delete_mcp23017_menu(self, parent: CursesMenu = None) -> None:
        rows = SQL._cursor.execute('SELECT * FROM I2C_MCP23017').fetchall()
        options = [f"Bus {row['bus']}, Addr 0x{row['address']:02X}" for row in rows]
        selection = SelectionMenu.get_selection(options, "Select MCP23017 to delete", parent=parent)
        if selection != -1:
            SQL._cursor.execute('DELETE FROM I2C_MCP23017 WHERE id = ?', (rows[selection]['id'],))
            SQL._conn.commit()
            self._show_message('Delete MCP23017', 'Chip deleted.', parent=parent)

    def _manage_ads1115(self, parent: CursesMenu = None) -> None:
        while True:
            menu = CursesMenu("Manage ADS1115 Chips")
            rows = SQL._cursor.execute('SELECT * FROM I2C_ADS1115').fetchall()
            for row in rows:
                label = f"Bus {row['bus']}, Addr 0x{row['address']:02X}"
                menu.append_item(MenuItem(label))

            menu.append_item(FunctionItem("Add new chip", self._add_ads1115, [menu], should_exit=True))
            if rows:
                menu.append_item(FunctionItem("Delete a chip", self._delete_ads1115_menu, [menu], should_exit=True))
            
            menu.show(parent=parent)
            if menu.selected_option == -1 or menu.selected_item == menu.exit_item:
                break

    def _add_ads1115(self, parent: CursesMenu = None) -> None:
        try:
            bus_str = self._text_input('Add ADS1115', 'I2C Bus:', default='1', parent=parent) or '1'
            bus = int(bus_str)
            addr_str = self._text_input('Add ADS1115', 'I2C Address (hex):', default='0x48', parent=parent) or '0x48'
            addr = int(addr_str, 16)
            SQL._cursor.execute('INSERT INTO I2C_ADS1115 (bus, address) VALUES (?,?)', (bus, addr))
            SQL._conn.commit()
            self._show_message('Add ADS1115', 'Chip added.', parent=parent)
        except ValueError:
            self._show_message('Add ADS1115', 'Invalid input.', parent=parent)

    def _delete_ads1115_menu(self, parent: CursesMenu = None) -> None:
        rows = SQL._cursor.execute('SELECT * FROM I2C_ADS1115').fetchall()
        options = [f"Bus {row['bus']}, Addr 0x{row['address']:02X}" for row in rows]
        selection = SelectionMenu.get_selection(options, "Select ADS1115 to delete", parent=parent)
        if selection != -1:
            SQL._cursor.execute('DELETE FROM I2C_ADS1115 WHERE id = ?', (rows[selection]['id'],))
            SQL._conn.commit()
            self._show_message('Delete ADS1115', 'Chip deleted.', parent=parent)

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

    def _load_preset(self, parent: CursesMenu = None) -> None:
        """Let the user pick a HAT preset and apply it to the database."""
        from ui.hat_presets import get_preset_names, get_display_name, preset_to_db_rows

        keys = get_preset_names()
        display_names = [get_display_name(k) for k in keys]
        selection = SelectionMenu.get_selection(display_names, "Load HAT Preset", parent=parent)

        
        if selection == -1:
            return
            
        key = keys[selection]
        rows = preset_to_db_rows(key)
        if not rows:
            self._show_message('Load HAT preset', 'Preset is empty or invalid.', parent=parent)
            return

        if not self._confirm(
            'Load HAT preset',
            f'Preset "{get_display_name(key)}" will create {len(rows)} mapping(s). Overwrite existing mappings for affected devices?',
            parent=parent,
        ):
            return

        # Group by device name and replace each device
        devices_affected = {r[0] for r in rows}
        for device_name in devices_affected:
            SQL.deleteDevice(device_name)
        SQL.createDevice(rows)
        self._show_message('Load HAT preset', f'Preset "{get_display_name(key)}" applied.', parent=parent)

    # ---------------------------------------------------------------------------
    # Import / Export
    # ---------------------------------------------------------------------------

    def _export_config(self) -> None:
        """Export the full config database to a JSON file."""
        default_path = '/opt/gpionext/config_backup.json'
        path = self._text_input('Export config', 'Export path:', default=default_path) or default_path
        try:
            data = SQL.exportToJson()
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            print(pcolor('green', f'  Exported {len(data)} rows to {path}'))
        except OSError as exc:
            print(pcolor('red', f'  Export failed: {exc}'))

    def _import_config(self, parent: CursesMenu = None) -> None:
        """Import a config from a JSON file, replacing the current database."""
        path = self._text_input('Import config', 'Import file path:', parent=parent)
        if not path:
            return
        if not os.path.isfile(path):
            self._show_message('Import config', f'File not found: {path}', parent=parent)
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self._show_message('Import config', f'Failed to read file: {exc}', parent=parent)
            return

        if not self._confirm('Import config', f'Replace current config with {len(data)} entries from {path}?', parent=parent):
            return
        SQL.importFromJson(data, replace=True)
        self._show_message('Import config', f'Imported {len(data)} entries.', parent=parent)

    # ---------------------------------------------------------------------------
    # Selection helpers
    # ---------------------------------------------------------------------------

    def _show_message(
        self,
        title: str,
        message: str,
        parent: CursesMenu = None,
    ) -> None:
        """Show a menu-safe one-button message and wait for acknowledgement."""
        SelectionMenu.get_selection(["OK"], title, message, parent=parent)

    def _confirm(
        self,
        title: str,
        message: str,
        parent: CursesMenu = None,
        default_no: bool = True,
    ) -> bool:
        """Ask a yes/no question with a SelectionMenu and return True for Yes."""
        options = ["No", "Yes"] if default_no else ["Yes", "No"]
        selection = SelectionMenu.get_selection(options, title, message, parent=parent)
        return selection == (1 if default_no else 0)

    def _choose_action(
        self,
        title: str,
        options: list[str],
        parent: CursesMenu = None,
    ) -> int:
        """Show an action list and return the selected option index, or -1."""
        return SelectionMenu.get_selection(options, title, parent=parent)

    def _text_input(
        self,
        title: str,
        prompt: str,
        default: str = "",
        parent: CursesMenu = None,
    ) -> str | None:
        """Temporarily leave curses mode, read one line, then restore the menu."""
        active_menu = parent or CursesMenu.currently_active_menu
        had_curses = CursesMenu.stdscr is not None
        suffix = f" [{default}]" if default else ""

        try:
            if had_curses:
                curses.def_prog_mode()
                curses.endwin()
            print(f"\n{title}")
            value = input(f"  {prompt}{suffix} ").strip()
            return value or default
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            if had_curses:
                curses.reset_prog_mode()
                if active_menu and active_menu.screen:
                    active_menu.draw()

    def _show_status(self, title: str, message: str) -> None:
        """Display a transient status message without leaving curses mode."""
        if CursesMenu.stdscr is None:
            print(f"{title}: {message}")
            return

        screen = CursesMenu.stdscr
        max_rows, max_cols = screen.getmaxyx()
        screen.erase()
        screen.addstr(1, 2, title[: max_cols - 4], curses.A_STANDOUT)
        for index, line in enumerate(message.splitlines() or [""]):
            row = 3 + index
            if row >= max_rows - 1:
                break
            screen.addstr(row, 2, line[: max_cols - 4])
        screen.refresh()

    def _capture_pins(self, label: str) -> str:
        """Prompt for a GPIO hold, wait for capture/release, and return DB pin text."""
        self._show_status('Waiting for GPIO input', f'Hold pin(s) for {label}')
        pins = self.wait_for_pin()
        pins_str = self._pins_to_str(pins)
        self._show_status('Pin captured', f'{label}: {pins_str}\nRelease all buttons to continue')
        self.wait_for_release()
        return pins_str

    def _ask_axis_count(self, parent: CursesMenu = None) -> int:
        """
        Ask how many D-pads/joystick axes the joypad device should have.

        Returns:
            int: number of axes (1-4)
        """
        options = [f"{i} D-pad{'s' if i > 1 else ''} / joystick{'s' if i > 1 else ''}" for i in range(1, 5)]
        selection = SelectionMenu.get_selection(options, "How many D-pads/joysticks?", parent=parent)
        if selection == -1 or selection >= len(options):
            return 1
        return selection + 1

    def _select_buttons_to_configure(self, parent: CursesMenu = None) -> list[tuple[str, int]]:
        """
        Show BUTTON_LIST and let the user pick which buttons to configure.
        Returns the selected (name, evdev_code) pairs.
        """
        names = [name for name, _ in BUTTON_LIST]
        selected_labels = MultiSelect.get_selection(
            names,
            "Select buttons to configure",
            "Space: toggle | Enter: continue",
            parent=parent,
        )
        if selected_labels is None or selected_labels == [] or selected_labels == [-1]:
            return []
        selected_labels: set[str] = set(selected_labels)

        return [btn for btn in BUTTON_LIST if btn[0] in selected_labels]

    def _select_keys_to_configure(self, parent: CursesMenu = None) -> list[tuple[str, int]]:
        """
        Show KEY_LIST and let the user pick which keys to configure.
        Returns the selected (name, evdev_code) pairs.
        """
        names = [name for name, _ in KEY_LIST]
        selected_labels = MultiSelect.get_selection(
            names,
            "Select keys to configure",
            "Space: toggle | Enter: continue",
            parent=parent,
        )
        if selected_labels is None or selected_labels == [] or selected_labels == [-1]:
            return []
        selected_labels: set[str] = set(selected_labels)

        return [key for key in KEY_LIST if key[0] in selected_labels]

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
