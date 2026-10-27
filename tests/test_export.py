"""Tests for export: CSV quoting, formula injection, JSON shape and file writing."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest

from space_analyzer import export
from space_analyzer.models import (
    ActionFailure,
    ActionReport,
    Category,
    CleanupItem,
    DuplicateGroup,
    FileEntry,
    Risk,
    ScanNode,
    ScanResult,
    SkippedPath,
)


def _scan() -> ScanResult:
    root = ScanNode(path="C:\\", name="C:\\", size=3000, file_count=3, dir_count=1)
    child = ScanNode(path="C:\\Users", name="Users", size=3000, file_count=3, dir_count=0)
    root.children.append(child)
    return ScanResult(
        root="C:\\",
        root_node=root,
        total_size=3000,
        file_count=3,
        dir_count=1,
        largest_files=[FileEntry(path="C:\\big.bin", size=2000, modified=1_700_000_000.0)],
        skipped=[SkippedPath(path="C:\\System Volume Information", reason="access denied")],
        error_count=1,
        elapsed_seconds=2.5,
    )


def _item(**overrides) -> CleanupItem:
    defaults = dict(
        item_id="temp.user",
        title="Your temporary files",
        category=Category.TEMP,
        risk=Risk.SAFE,
        description="d",
        impact="i",
        regenerates="Yes",
        size_bytes=1000,
        file_count=4,
        requires_admin=False,
        default_selected=True,
    )
    defaults.update(overrides)
    return CleanupItem(**defaults)


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


class SafeCellTests(unittest.TestCase):
    """A spreadsheet must never run a cell as a formula."""

    def test_formula_starts_are_prefixed(self) -> None:
        for value in ("=cmd|calc", "+1+1", "-2+3", "@SUM(A1)"):
            self.assertEqual(export.safe_cell(value), "'" + value)

    def test_ordinary_text_is_unchanged(self) -> None:
        self.assertEqual(export.safe_cell("C:\\Users\\me"), "C:\\Users\\me")

    def test_a_number_is_unchanged(self) -> None:
        self.assertEqual(export.safe_cell(42), "42")

    def test_none_becomes_empty(self) -> None:
        self.assertEqual(export.safe_cell(None), "")

    def test_a_control_character_start_is_prefixed(self) -> None:
        self.assertEqual(export.safe_cell("\t=1+1"), "'\t=1+1")


class FormulaInjectionTests(unittest.TestCase):
    """The guard is applied to real report output, not only to safe_cell."""

    def test_a_dangerous_file_name_is_neutralised_in_csv(self) -> None:
        # A folder whose path starts with "=" is legal on Windows, and a spreadsheet
        # would run it as a formula. Row 0 is the header and row 1 the root, so the
        # child folder lands on row 2.
        scan = _scan()
        scan.root_node.children[0].path = "=cmd|' /C calc'!A0"
        rows = _csv_rows(export.scan_to_csv(scan))
        self.assertEqual(rows[2][0], "'=cmd|' /C calc'!A0")
        for cell in (cell for row in rows for cell in row):
            self.assertFalse(cell.startswith("="), cell)

    def test_a_path_that_starts_with_a_drive_letter_is_left_alone(self) -> None:
        # "C:\..." is not a formula, so the guard must not mangle ordinary paths.
        scan = _scan()
        scan.root_node.children[0].path = "C:\\=cmd|calc"
        rows = _csv_rows(export.scan_to_csv(scan))
        self.assertEqual(rows[2][0], "C:\\=cmd|calc")

    def test_a_dangerous_item_title_is_neutralised_in_csv(self) -> None:
        rows = _csv_rows(export.items_to_csv([_item(title="=HYPERLINK(1)")]))
        cells = [cell for row in rows for cell in row]
        self.assertIn("'=HYPERLINK(1)", cells)


class CsvShapeTests(unittest.TestCase):
    """Commas and quotes in a path must not shift the columns."""

    def test_every_cell_is_quoted(self) -> None:
        text = export.scan_to_csv(_scan())
        self.assertIn('"Path"', text)

    def test_a_path_with_a_comma_stays_one_cell(self) -> None:
        scan = _scan()
        scan.root_node.children[0].path = "C:\\Users\\me, you"
        rows = _csv_rows(export.scan_to_csv(scan))
        # Row 0 is the header, row 1 the root, so the child is row 2.
        self.assertEqual(rows[2][0], "C:\\Users\\me, you")

    def test_a_path_with_a_quote_stays_one_cell(self) -> None:
        scan = _scan()
        scan.root_node.children[0].path = 'C:\\Users\\a"b'
        rows = _csv_rows(export.scan_to_csv(scan))
        self.assertEqual(rows[2][0], 'C:\\Users\\a"b')

    def test_the_header_and_a_totals_row_are_present(self) -> None:
        rows = _csv_rows(export.scan_to_csv(_scan()))
        self.assertEqual(rows[0][0], "Path")
        self.assertEqual(rows[-1][1], "TOTAL")

    def test_line_endings_are_crlf(self) -> None:
        # Excel is happiest with CRLF, and csv.writer is told to use it.
        self.assertIn("\r\n", export.scan_to_csv(_scan()))


class JsonShapeTests(unittest.TestCase):
    """The JSON reports are valid and carry the fields a reader would want."""

    def test_scan_json_round_trips(self) -> None:
        payload = json.loads(export.scan_to_json(_scan()))
        self.assertEqual(payload["kind"], "scan")
        self.assertEqual(payload["total_size"], 3000)
        self.assertEqual(payload["tree"]["children"][0]["name"], "Users")
        self.assertEqual(payload["largest_files"][0]["path"], "C:\\big.bin")
        self.assertEqual(payload["skipped"][0]["reason"], "access denied")

    def test_suggestions_json_round_trips(self) -> None:
        payload = json.loads(export.items_to_json([_item(), _item(item_id="b", size_bytes=500)]))
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total_bytes"], 1500)
        self.assertEqual(payload["items"][0]["category"], "Temporary files")

    def test_report_json_round_trips(self) -> None:
        report = ActionReport(
            action="delete_paths",
            title="Temp",
            removed_count=2,
            bytes_removed=100,
            failed=[ActionFailure("C:\\x", "access denied")],
            messages=["done"],
            requires_restart=True,
        )
        payload = json.loads(export.reports_to_json([report]))
        self.assertEqual(payload["total_bytes_removed"], 100)
        self.assertEqual(payload["reports"][0]["failed"][0]["reason"], "access denied")
        self.assertTrue(payload["reports"][0]["requires_restart"])

    def test_duplicates_json_round_trips(self) -> None:
        group = DuplicateGroup(digest="a" * 64, size=100, paths=["C:\\a", "C:\\b", "C:\\c"])
        payload = json.loads(export.duplicates_to_json([group]))
        self.assertEqual(payload["group_count"], 1)
        self.assertEqual(payload["reclaimable_bytes"], 200)

    def test_json_is_utf8_and_keeps_non_ascii(self) -> None:
        payload = json.loads(export.items_to_json([_item(title="Café — données")]))
        self.assertEqual(payload["items"][0]["title"], "Café — données")


class DuplicatesCsvTests(unittest.TestCase):
    """Each copy gets a row; only the second and later copies count as reclaimable."""

    def test_first_copy_is_not_counted_as_reclaimable(self) -> None:
        group = DuplicateGroup(digest="a" * 64, size=100, paths=["C:\\a", "C:\\b"])
        rows = _csv_rows(export.duplicates_to_csv([group]))
        self.assertEqual(rows[1][5], "0")
        self.assertEqual(rows[2][5], "100")

    def test_every_copy_is_listed(self) -> None:
        group = DuplicateGroup(digest="a" * 64, size=5, paths=["a", "b", "c"])
        rows = _csv_rows(export.duplicates_to_csv([group]))
        self.assertEqual(len(rows), 4)  # header plus three copies


class WriteTests(unittest.TestCase):
    """Files are written where asked, with the format taken from the extension."""

    def setUp(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="space_export_test_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, True))

    def test_csv_gets_a_utf8_bom_for_excel(self) -> None:
        path = os.path.join(self.folder, "scan.csv")
        export.write_scan(path, _scan())
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(3), b"\xef\xbb\xbf")

    def test_json_is_written_without_a_bom(self) -> None:
        path = os.path.join(self.folder, "scan.json")
        export.write_scan(path, _scan())
        with open(path, "rb") as handle:
            first = handle.read(1)
        self.assertEqual(first, b"{")

    def test_the_extension_decides_the_format(self) -> None:
        csv_path = os.path.join(self.folder, "scan.CSV")
        export.write_scan(csv_path, _scan())
        with open(csv_path, encoding="utf-8-sig") as handle:
            self.assertIn("Path,", handle.read().replace('"', ""))

    def test_a_missing_parent_folder_is_created(self) -> None:
        path = os.path.join(self.folder, "nested", "deeper", "scan.json")
        export.write_scan(path, _scan())
        self.assertTrue(os.path.exists(path))

    def test_items_and_reports_are_written(self) -> None:
        items_path = os.path.join(self.folder, "items.json")
        export.write_items(items_path, [_item()])
        self.assertTrue(os.path.exists(items_path))

        reports_path = os.path.join(self.folder, "reports.csv")
        export.write_reports(reports_path, [ActionReport(action="x", bytes_removed=5)])
        self.assertTrue(os.path.exists(reports_path))

        dupes_path = os.path.join(self.folder, "dupes.json")
        export.write_duplicates(dupes_path, [])
        self.assertTrue(os.path.exists(dupes_path))


class DefaultNameTests(unittest.TestCase):
    """The suggested name carries the kind and a timestamp."""

    def test_the_name_contains_the_kind_and_an_extension(self) -> None:
        name = export.default_name("scan")
        self.assertTrue(name.startswith("space-analyzer-scan-"))
        self.assertTrue(name.endswith(".csv"))

    def test_an_extension_can_be_chosen(self) -> None:
        self.assertTrue(export.default_name("items", "json").endswith(".json"))


if __name__ == "__main__":
    unittest.main()