import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

import config.SQL as SQL  # noqa: E402
from config.constants import available_i2c_pins, pcf8574_pin_id  # noqa: E402


class Pcf8574ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        SQL.init(str(Path(self._tmpdir.name) / "config.db"))

    def tearDown(self):
        if SQL._conn is not None:
            SQL._conn.close()
        SQL._conn = None
        SQL._cursor = None
        self._tmpdir.cleanup()

    def test_pcf8574_pin_ids_map_to_distinct_virtual_range(self):
        self.assertEqual("i2c-0x20-P0", pcf8574_pin_id(0x20, 0))
        self.assertEqual("i2c-0x20-P7", pcf8574_pin_id(0x20, 7))
        self.assertEqual(192, SQL.pin_value_to_vpin("i2c-0x20-P0"))
        self.assertEqual(199, SQL.pin_value_to_vpin("i2c-0x20-P7"))
        self.assertEqual(200, SQL.pin_value_to_vpin("i2c-0x21-P0"))

    def test_available_i2c_pins_includes_pcf8574_pins(self):
        pins = available_i2c_pins(pcf8574_addresses=[0x20])
        self.assertEqual("i2c-0x20-P0", pins[0])
        self.assertEqual("i2c-0x20-P7", pins[-1])
        self.assertEqual(8, len(pins))

    def test_build_config_includes_pcf8574_when_i2c_enabled(self):
        SQL._cursor.execute(
            "INSERT INTO I2C_PCF8574 (bus, address, int_pin) VALUES (?,?,?)",
            (1, 0x20, 7),
        )
        SQL._conn.commit()

        config = SQL.buildConfigDict(SimpleNamespace(use_i2c=True, pins=[]))

        self.assertEqual([{"bus": 1, "address": 0x20, "int_pin": 7}], config["i2c_pcf8574"])


if __name__ == "__main__":
    unittest.main()
