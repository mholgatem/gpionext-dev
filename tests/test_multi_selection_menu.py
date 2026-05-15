import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python" / "ui" / "cursesmenu"))

from cursesmenu import MultiSelect  # noqa: E402
from cursesmenu.items import SelectionItem  # noqa: E402


class MultiSelectMenuTests(unittest.TestCase):
    def test_select_many_returns_checked_labels_without_indexing_by_list(self):
        menu = MultiSelect(["A", "B", "C"], show_exit_option=False)
        for item in menu.items:
            self.assertIsInstance(item, SelectionItem)
            item.defaultText = item.text
            item.checked = item.text in {"A", "C"}

        menu.current_option = 1
        menu.select_many()

        self.assertEqual(["A", "C"], menu.selected_option)
        self.assertEqual(["A", "C"], menu.returned_value)
        self.assertTrue(menu.should_exit)

    def test_select_many_exit_item_returns_cancellation_marker(self):
        menu = MultiSelect(["A", "B"], show_exit_option=True)
        for item in menu.items:
            if isinstance(item, SelectionItem):
                item.defaultText = item.text
                item.checked = True
        menu.add_exit()
        menu.current_option = len(menu.items) - 1

        menu.select_many()

        self.assertEqual([-1], menu.selected_option)
        self.assertEqual([-1], menu.returned_value)
        self.assertTrue(menu.should_exit)


if __name__ == "__main__":
    unittest.main()
