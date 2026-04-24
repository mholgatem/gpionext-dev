"""
SQL.py — SQLite CRUD for GPIOnext device/button configuration.

Schema (unchanged from reference):
    GPIOnext(
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        device  TEXT,    -- 'Joypad 1'..'Joypad 4', 'Keyboard', 'Commands'
        name    TEXT,    -- human label e.g. 'START', 'volume_up'
        type    TEXT,    -- 'BUTTON' | 'KEY' | 'AXIS' | 'COMMAND'
        command TEXT,    -- evdev code (int str) or bash string or axis tuple str
        pins    TEXT     -- single int or tuple str e.g. '11' or '(11, 13)'
    )

All functions preserve the same signatures as the reference SQL.py so existing
callers (config_manager.py, gpionext.py) work without changes.
"""
import os
import sqlite3

# ---------------------------------------------------------------------------
# Install path — updated from /home/pi/gpionext to /opt/gpionext
# ---------------------------------------------------------------------------

INSTALL_PATH = '/opt/gpionext'
DEFAULT_DB_PATH = os.path.join(INSTALL_PATH, 'config', 'config.db')

# Module-level connection (initialised by init())
_conn: sqlite3.Connection | None = None
_cursor: sqlite3.Cursor | None = None


def _row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    """Convert a sqlite3 row tuple to a dict keyed by column name."""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init(db_path: str | None = None) -> None:
    """
    Open (or create) the SQLite database and ensure the GPIOnext table exists.
    Must be called once before any other function in this module.

    Parameters:
        db_path (str|None): override the database file path. Uses
                            DEFAULT_DB_PATH when None, falling back to a
                            local ./config/config.db if /opt/gpionext is absent.
    """
    global _conn, _cursor

    if db_path is None:
        db_path = _resolve_db_path()

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = _row_factory
    _cursor = _conn.cursor()

    _cursor.execute(
        'CREATE TABLE IF NOT EXISTS GPIOnext ('
        '  id      INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE,'
        '  device  TEXT,'
        '  name    TEXT,'
        '  type    TEXT,'
        '  command TEXT,'
        '  pins    TEXT'
        ')'
    )
    _conn.commit()


def _resolve_db_path() -> str:
    """
    Determine the database path.
    Prefers DEFAULT_DB_PATH (/opt/gpionext/config/config.db).
    Falls back to ./config/config.db relative to this file's location,
    which allows running from the source tree during development.

    Returns:
        str: absolute path to config.db
    """
    if os.path.isdir(os.path.dirname(DEFAULT_DB_PATH)):
        return DEFAULT_DB_PATH
    # Development fallback: config/ next to this file
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(here, 'config.db')


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def getDevices(device_names: list[str]) -> list[list[dict]]:
    """
    Load raw DB rows for each device name. Returns one list per device,
    preserving order. Empty inner lists mean that device has no mappings.

    Parameters:
        device_names (list[str]): e.g. ['Joypad 1', 'Keyboard', 'Commands']

    Returns:
        list[list[dict]]: outer list mirrors device_names; inner list is DB rows.
    """
    _require_init()
    result = []
    for name in device_names:
        rows = _cursor.execute(
            'SELECT * FROM GPIOnext WHERE device = ?', (name,)
        ).fetchall()
        result.append(rows)
    return result


def getDevice(device_name: str) -> list[dict]:
    """
    Load all DB rows for a single device.

    Parameters:
        device_name (str): exact device name (LIKE match for partial names)

    Returns:
        list[dict]: rows for the device; empty list if none.
    """
    _require_init()
    return _cursor.execute(
        'SELECT * FROM GPIOnext WHERE device LIKE ?', (device_name,)
    ).fetchall()


def getDeviceRaw(device_name: str) -> list[dict]:
    """
    Alias for getDevice; kept for backward compatibility with config_manager.py.

    Parameters:
        device_name (str): exact or LIKE-pattern device name

    Returns:
        list[dict]: raw DB rows
    """
    return getDevice(device_name)


def getAllRows() -> list[dict]:
    """
    Return every row in the database. Used by import/export and the live pin view.

    Returns:
        list[dict]: all rows ordered by id.
    """
    _require_init()
    return _cursor.execute('SELECT * FROM GPIOnext ORDER BY id').fetchall()


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def updateEntry(entry: dict) -> None:
    """
    Insert or replace a single row. The dict must contain all column keys:
    id, device, name, type, command, pins.
    Pass id=None to insert a new row (SQLite auto-increments).

    Parameters:
        entry (dict): keys: id, device, name, type, command, pins
    """
    _require_init()
    _cursor.execute(
        'INSERT OR REPLACE INTO GPIOnext (id, device, name, type, command, pins) '
        'VALUES (:id, :device, :name, :type, :command, :pins)',
        entry
    )
    _conn.commit()


def createDevice(rows: list[tuple]) -> None:
    """
    Bulk-insert multiple rows for a new device.

    Parameters:
        rows (list[tuple]): each tuple is (device, name, type, command, pins)
    """
    _require_init()
    _cursor.executemany(
        'INSERT INTO GPIOnext (device, name, type, command, pins) VALUES (?,?,?,?,?)',
        rows
    )
    _conn.commit()


def deleteEntry(entry: dict) -> None:
    """
    Delete a single row by id.

    Parameters:
        entry (dict): must contain 'id' key
    """
    _require_init()
    _cursor.execute('DELETE FROM GPIOnext WHERE id = :id', entry)
    _conn.commit()


def deleteDevice(device_name: str) -> None:
    """
    Delete all rows for a device (used by 'Clear Device' menu option).

    Parameters:
        device_name (str): exact device name to delete
    """
    _require_init()
    _cursor.execute('DELETE FROM GPIOnext WHERE device = ?', (device_name,))
    _conn.commit()


# ---------------------------------------------------------------------------
# Import / Export (JSON)
# ---------------------------------------------------------------------------

def exportToJson() -> list[dict]:
    """
    Return all rows as a list of dicts suitable for json.dumps.

    Returns:
        list[dict]: all rows with string values (as stored in DB)
    """
    return getAllRows()


def importFromJson(rows: list[dict], replace: bool = True) -> None:
    """
    Import rows from a JSON export. Optionally clears existing data first.

    Parameters:
        rows    (list[dict]): rows from a previous exportToJson() call
        replace (bool): if True, wipes the database before importing
    """
    _require_init()
    if replace:
        _cursor.execute('DELETE FROM GPIOnext')
    for row in rows:
        # Strip 'id' so SQLite auto-assigns; preserves relative order
        entry = {k: v for k, v in row.items() if k != 'id'}
        _cursor.execute(
            'INSERT INTO GPIOnext (device, name, type, command, pins) '
            'VALUES (:device, :name, :type, :command, :pins)',
            entry
        )
    _conn.commit()


# ---------------------------------------------------------------------------
# Config dict builder (used by gpionext.py → GpioCore.start())
# ---------------------------------------------------------------------------

def buildConfigDict(args) -> dict:
    """
    Build the config dict that gpionext.py passes to gpionext_core.GpioCore.start().
    Translates raw DB rows into the format expected by lib.rs parse_peripherals().

    Parameters:
        args: argparse Namespace with combo_delay, key_hold_delay, debounce,
              pulldown, pins, dev, debug attributes

    Returns:
        dict: config dict with 'peripherals', 'combo_delay', 'key_hold_delay',
              'debounce', 'pulldown', 'pins', 'skip_pins' keys
    """
    from config.constants import DEVICE_INDEX

    rows = getAllRows()
    peripherals = []

    for row in rows:
        device_name = row['device']
        device_index = DEVICE_INDEX.get(device_name, 5)

        # pins stored as '11' (single) or '(11, 13)' (combo/tuple)
        raw_pins = row['pins']
        try:
            pins_val = eval(raw_pins)
            if isinstance(pins_val, int):
                pins = [pins_val]
            else:
                pins = list(pins_val)
        except Exception:
            pins = []

        peripherals.append({
            'name':         row['name'],
            'device_index': device_index,
            'type':         row['type'],
            'command':      str(row['command']),
            'pins':         pins,
        })

    skip_pins = []
    try:
        from config.hat_detect import detect_audio_hat
        hat = detect_audio_hat()
        if hat:
            skip_pins = hat.get('reserved_pins', [])
    except ImportError:
        pass

    return {
        'peripherals':     peripherals,
        'combo_delay':     int(args.combo_delay),
        'key_hold_delay':  int(getattr(args, 'key_hold_delay', 350)),
        'debounce':        int(args.debounce),
        'pulldown':        bool(args.pulldown),
        'pins':            list(args.pins),
        'skip_pins':       skip_pins,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_init() -> None:
    """Raise RuntimeError if init() has not been called."""
    if _conn is None:
        raise RuntimeError('SQL.init() must be called before using any SQL functions')
