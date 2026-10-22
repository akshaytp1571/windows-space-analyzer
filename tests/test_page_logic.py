"""Tests for the pure rules behind the cleanup pages. They run without PySide6."""

from __future__ import annotations

import unittest

from space_analyzer.models import (
    ActionFailure,
    ActionKind,
    ActionReport,
    Category,
    CleanupItem,
    DuplicateGroup,
    FileEntry,
    Risk,
)
from space_analyzer.ui import page_logic as logic


def make_item(
    item_id: str,
    action: ActionKind = ActionKind.DELETE_PATHS,
    size: int = 0,
    known: bool = True,
    warnings: list[str] | None = None,
) -> CleanupItem:
    """Build a minimal CleanupItem for the rules under test."""
    return CleanupItem(
        item_id=item_id,
        title=item_id.title(),
        category=Category.TEMP,
        risk=Risk.SAFE,
        description="",
        impact="",
        regenerates="",
        action=action,
        size_bytes=size,
        size_known=known,
        warnings=list(warnings or []),
    )


class CleanupOrderTests(unittest.TestCase):
    def test_recycle_bin_runs_last_and_other_items_keep_their_order(self) -> None:
        items = [
            make_item("bin", ActionKind.EMPTY_RECYCLE_BIN),
            make_item("a"),
            make_item("b", ActionKind.CLEAN_UPDATE_CACHE),
            make_item("c"),
        ]
        ordered = logic.cleanup_order(items)
        self.assertEqual([item.item_id for item in ordered], ["a", "b", "c", "bin"])


class PermanenceTests(unittest.TestCase):
    def test_permanent_actions_are_flagged(self) -> None:
        for action in (
            ActionKind.EMPTY_RECYCLE_BIN,
            ActionKind.DISABLE_HIBERNATION,
            ActionKind.DELETE_RESTORE_POINTS,
            ActionKind.CLEAN_WINDOWS_OLD,
        ):
            with self.subTest(action=action):
                self.assertTrue(logic.is_permanent([make_item("x", action)]))

    def test_recycle_and_information_actions_are_not_permanent(self) -> None:
        for action in (ActionKind.DELETE_PATHS, ActionKind.CLEAN_UPDATE_CACHE, ActionKind.NONE):
            with self.subTest(action=action):
                self.assertFalse(logic.is_permanent([make_item("x", action)]))


class SelectionTests(unittest.TestCase):
    def test_unknown_sizes_and_information_items_are_not_counted(self) -> None:
        items = [
            make_item("a", size=100),
            make_item("b", size=999, known=False),
            make_item("c", ActionKind.NONE, size=50),
        ]
        self.assertEqual(logic.selection_bytes(items), 100)

    def test_summary_text(self) -> None:
        self.assertEqual(logic.selection_summary(0, 0), "Nothing selected")
        self.assertEqual(logic.selection_summary(2, 1536), "2 selected, about 1.5 KB")


class ConfirmationTextTests(unittest.TestCase):
    def test_details_list_size_then_warnings(self) -> None:
        lines = logic.cleanup_details(
            [make_item("a", size=2048, warnings=["Careful"]), make_item("b", known=False)]
        )
        self.assertEqual(lines, ["A: 2.0 KB", "Careful", "B: unknown"])

    def test_recycle_only_selection_gets_the_recycle_notice(self) -> None:
        self.assertEqual(logic.cleanup_notices([make_item("a")]), [logic.RECYCLE_NOTICE])

    def test_bin_emptied_in_the_same_run_replaces_the_recycle_notice(self) -> None:
        notices = logic.cleanup_notices(
            [make_item("a"), make_item("bin", ActionKind.EMPTY_RECYCLE_BIN)]
        )
        self.assertEqual(notices, [logic.EMPTY_BIN_NOTICE, logic.PERMANENT_NOTICE])

    def test_hibernation_gets_only_the_permanent_notice(self) -> None:
        notices = logic.cleanup_notices([make_item("h", ActionKind.DISABLE_HIBERNATION)])
        self.assertEqual(notices, [logic.PERMANENT_NOTICE])


class RecycleBinPromptTests(unittest.TestCase):
    def test_prompt_only_when_files_moved_and_bin_not_emptied(self) -> None:
        moved = ActionReport(action="delete_paths", removed_count=2)
        nothing = ActionReport(action="delete_paths", removed_count=0)
        self.assertTrue(logic.moved_to_recycle_bin([make_item("a")], [moved]))
        self.assertFalse(logic.moved_to_recycle_bin([make_item("a")], [nothing]))
        with_bin = [make_item("a"), make_item("bin", ActionKind.EMPTY_RECYCLE_BIN)]
        self.assertFalse(logic.moved_to_recycle_bin(with_bin, [moved, ActionReport(action="x")]))
        hibernate = [make_item("h", ActionKind.DISABLE_HIBERNATION)]
        self.assertFalse(logic.moved_to_recycle_bin(hibernate, [moved]))


class KeepOneCopyTests(unittest.TestCase):
    def test_blocked_only_when_every_copy_is_checked(self) -> None:
        self.assertFalse(logic.keep_one_blocked([]))
        self.assertFalse(logic.keep_one_blocked([False, True]))
        self.assertFalse(logic.keep_one_blocked([False, False]))
        self.assertTrue(logic.keep_one_blocked([True, True]))
        self.assertTrue(logic.keep_one_blocked([True]))

    def test_keep_oldest_leaves_only_the_first_copy_unchecked(self) -> None:
        self.assertEqual(logic.keep_oldest_flags(0), [])
        self.assertEqual(logic.keep_oldest_flags(1), [False])
        self.assertEqual(logic.keep_oldest_flags(3), [False, True, True])


class FailureFilterTests(unittest.TestCase):
    def test_reported_failures_are_dropped_from_the_row_list(self) -> None:
        report = ActionReport(action="recycle", failed=[ActionFailure("b", "Protected")])
        self.assertEqual(logic.paths_not_failed(["a", "b", "c"], [report]), ["a", "c"])
        self.assertEqual(logic.paths_not_failed(["a"], []), ["a"])


class AppDataTests(unittest.TestCase):
    def test_appdata_folders_are_detected_in_either_slash_style(self) -> None:
        self.assertTrue(logic.is_appdata_path(r"C:\Users\a\AppData\Local\x.dat"))
        self.assertTrue(logic.is_appdata_path("C:/Users/a/appdata/roaming/x.dat"))

    def test_a_file_name_that_only_contains_appdata_is_not_detected(self) -> None:
        self.assertFalse(logic.is_appdata_path(r"C:\Users\a\Documents\appdata.txt"))


class LargeFileFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = [
            FileEntry(r"C:\Data\movie.mkv", 5_000, 1.0),
            FileEntry(r"C:\Data\small.txt", 10, 1.0),
            FileEntry(r"C:\Secret\huge.bin", 9_000, 1.0),
            FileEntry(r"C:\Data\Backup.zip", 7_000, 1.0),
        ]

    def test_size_and_text_filters_and_largest_first_order(self) -> None:
        rows = logic.filter_large_files(
            self.entries, min_size=100, text="DATA", is_hidden=lambda path: False
        )
        self.assertEqual(
            [entry.path for entry in rows],
            [r"C:\Data\Backup.zip", r"C:\Data\movie.mkv"],
        )

    def test_hidden_paths_are_removed_even_without_text(self) -> None:
        rows = logic.filter_large_files(
            self.entries,
            min_size=100,
            text="",
            is_hidden=lambda path: path.startswith(r"C:\Secret"),
        )
        self.assertEqual(
            [entry.path for entry in rows],
            [r"C:\Data\Backup.zip", r"C:\Data\movie.mkv"],
        )


class TextTests(unittest.TestCase):
    def test_duplicate_group_heading(self) -> None:
        group = DuplicateGroup(digest="d", size=1024 * 1024, paths=["a", "b", "c"])
        self.assertEqual(
            logic.group_summary(group),
            "3 copies, 1.0 MB each, 2.0 MB can be freed",
        )

    def test_long_paths_are_shortened_to_the_limit(self) -> None:
        self.assertEqual(logic.shorten_path("C:\\short"), "C:\\short")
        long_path = "C:\\" + "x" * 100
        short = logic.shorten_path(long_path, limit=20)
        self.assertEqual(len(short), 20)
        self.assertTrue(short.startswith("..."))
        self.assertTrue(long_path.endswith(short[3:]))

    def test_size_choices_are_in_ascending_order(self) -> None:
        for choices in (logic.LARGE_FILE_SIZES, logic.DUPLICATE_SIZES):
            with self.subTest(choices=choices):
                sizes = [size for _label, size in choices]
                self.assertEqual(sizes, sorted(sizes))


if __name__ == "__main__":
    unittest.main()
