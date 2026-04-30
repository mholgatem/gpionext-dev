"""
live_pin_view.py — Real-time GPIO pin monitor panel.

Displays all configured BOARD pins in a full-screen curses panel.
Each pin row shows:
  - Pin number
  - Current state (pressed ● or idle ○)
  - What the pin is mapped to (device + action name), or 'unmapped'

Actively-pressed pins are highlighted. The display refreshes every ~50ms.
Press 'q' or ESC to return to the config menu.

Usage:
    from ui.live_pin_view import LivePinView
    with LivePinView(pins, db_rows) as view:
        view.run()
"""
import curses
import time
import sys
import os

_UI_DIR = os.path.dirname(os.path.realpath(__file__))
_PYTHON_DIR = os.path.dirname(_UI_DIR)
_INSTALL_ROOT = os.path.dirname(_PYTHON_DIR)
sys.path.insert(0, _PYTHON_DIR)
sys.path.insert(0, _INSTALL_ROOT)

import config.SQL as SQL

try:
    import gpionext_core
    _HAS_CORE = True
except ImportError:
    _HAS_CORE = False

# ---------------------------------------------------------------------------
# Pin label builder
# ---------------------------------------------------------------------------

def _build_pin_labels(all_pins: list[int], db_rows: list[dict]) -> dict[int, str]:
    """
    Build a map from BOARD pin number → display label ("Joypad 1 → START").

    Parameters:
        all_pins (list[int]): all pins to display
        db_rows  (list[dict]): all rows from SQL.getAllRows()

    Returns:
        dict[int, str]: pin → label string
    """
    labels: dict[int, str] = {p: 'unmapped' for p in all_pins}

    for row in db_rows:
        raw_pins = row.get('pins', '')
        try:
            pins_val = eval(raw_pins)
            pin_list = [pins_val] if isinstance(pins_val, int) else list(pins_val)
        except Exception:
            continue

        label = f"{row['device']} \u2192 {row['name']}"
        for pin in pin_list:
            if pin in labels:
                existing = labels[pin]
                if existing == 'unmapped':
                    labels[pin] = label
                else:
                    # Pin shared by multiple mappings (combo member)
                    labels[pin] = existing + ' / ' + label

    return labels


# ---------------------------------------------------------------------------
# Main view class
# ---------------------------------------------------------------------------

class LivePinView:
    """
    Context manager wrapping the curses live pin monitor.

    Parameters:
        pins    (list[int])  : BOARD pin numbers to display
        db_rows (list[dict]) : all rows from SQL.getAllRows() for label lookup
    """

    # Refresh interval in seconds
    REFRESH_INTERVAL = 0.05  # 50ms → 20 FPS

    def __init__(self, pins: list[int], db_rows: list[dict]) -> None:
        self.pins = sorted(pins)
        self.labels = _build_pin_labels(pins, db_rows)
        self._stdscr = None

    def __enter__(self) -> 'LivePinView':
        return self

    def __exit__(self, *args) -> None:
        pass  # curses.wrapper handles cleanup

    def run(self) -> None:
        """Launch the full-screen pin monitor. Blocks until user presses q/ESC."""
        curses.wrapper(self._curses_main)

    def _curses_main(self, stdscr: curses.window) -> None:
        """
        Main curses loop. Draws pin states every REFRESH_INTERVAL seconds.

        Parameters:
            stdscr: curses standard screen provided by curses.wrapper
        """
        self._stdscr = stdscr
        curses.curs_set(0)   # hide cursor
        stdscr.nodelay(True) # non-blocking getch
        stdscr.timeout(50)   # 50ms key poll

        # Color pairs: 1=normal, 2=pressed (bright), 3=header, 4=dim
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE,  -1)
        curses.init_pair(2, curses.COLOR_GREEN,  -1)  # pressed pin
        curses.init_pair(3, curses.COLOR_CYAN,   -1)  # header

        # Safe fallback for "dim" color (bright black / grey)
        # If terminal supports 16+ colors, use color 8. Otherwise fallback to white.
        dim_color = curses.COLOR_WHITE
        if curses.COLORS > 8:
            dim_color = 8  # Bright Black / Grey

        curses.init_pair(4, dim_color, -1)

        dim_attr = curses.color_pair(4)
        if curses.COLORS <= 8:
            dim_attr |= curses.A_DIM

        while True:
            key = stdscr.getch()
            if key in (ord('q'), ord('Q'), 27):  # q, Q, ESC
                break

            if _HAS_CORE:
                res = gpionext_core.get_pin_states()
                bitmask = res[0] | (res[1] << 64) | (res[2] << 128)
            else:
                bitmask = 0
            self._draw(stdscr, bitmask, dim_attr)
            time.sleep(self.REFRESH_INTERVAL)

    def _draw(self, stdscr: curses.window, bitmask: int, dim_attr: int) -> None:
        """
        Redraw the entire screen with current pin states.

        Parameters:
            stdscr   : curses window
            bitmask  : current pressed-pin bitmask from gpionext_core
            dim_attr : attributes for dim/decorative elements
        """
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        # Header
        title = ' GPIOnext \u2014 Live Pin Monitor  (q = exit) '
        stdscr.addstr(0, 0, title.center(max_x), curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(1, 0, '\u2500' * max_x, dim_attr)

        # Column headers
        col_headers = f"  {'PIN':>4}   {'ST':2}  {'MAPPED TO':<40}"
        stdscr.addstr(2, 0, col_headers, curses.color_pair(3))
        stdscr.addstr(3, 0, '\u2500' * max_x, dim_attr)

        row_y = 4
        for pin in self.pins:
            if row_y >= max_y - 2:
                stdscr.addstr(row_y, 0, '  ... (resize terminal to see more pins)',
                              dim_attr)
                break

            pressed = bool(bitmask & (1 << pin))
            state_char = '\u25cf' if pressed else '\u25cb'  # \u25cf or \u25cb
            label = self.labels.get(pin, 'unmapped')

            # Truncate label to available width
            label_width = max_x - 14
            if len(label) > label_width:
                label = label[:label_width - 1] + '\u2026'

            line = f"  {'BOARD ' + str(pin):>10}   {state_char}   {label}"

            if pressed:
                attr = curses.color_pair(2) | curses.A_BOLD
            elif label == 'unmapped':
                attr = dim_attr
            else:
                attr = curses.color_pair(1)

            try:
                stdscr.addstr(row_y, 0, line, attr)
            except curses.error:
                pass  # terminal too narrow; skip
            row_y += 1

        # Footer
        footer = ' Press q or ESC to return to the menu '
        try:
            stdscr.addstr(max_y - 1, 0, footer.center(max_x), dim_attr)
        except curses.error:
            pass

        stdscr.refresh()
