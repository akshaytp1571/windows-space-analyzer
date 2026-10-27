"""Tests for the file-type breakdown shown on the Large files page."""

from __future__ import annotations

import unittest

from space_analyzer.models import FileEntry
from space_analyzer.ui import breakdown as bd


def _file(path: str, size: int = 100) -> FileEntry:
    return FileEntry(path=path, size=size, modified=0.0)


class ClassifyTests(unittest.TestCase):
    """Extensions map to readable kinds, and unknown ones keep their own name."""

    def test_known_extensions_map_to_their_family(self) -> None:
        self.assertEqual(bd.classify("C:\\a\\film.mp4"), "video")
        self.assertEqual(bd.classify("C:\\a\\shot.PNG"), "image")
        self.assertEqual(bd.classify("C:\\a\\song.flac"), "audio")
        self.assertEqual(bd.classify("C:\\a\\pack.7z"), "archive")
        self.assertEqual(bd.classify("C:\\a\\setup.msi"), "installer")
        self.assertEqual(bd.classify("C:\\a\\report.pdf"), "document")
        self.assertEqual(bd.classify("C:\\a\\main.py"), "code")
        self.assertEqual(bd.classify("C:\\a\\store.sqlite"), "database")
        self.assertEqual(bd.classify("C:\\a\\disk.vhdx"), "disk image")
        self.assertEqual(bd.classify("C:\\a\\trace.etl"), "log")

    def test_case_is_ignored(self) -> None:
        self.assertEqual(bd.classify("C:\\a\\FILM.MP4"), "video")

    def test_an_unknown_extension_keeps_its_name(self) -> None:
        self.assertEqual(bd.classify("C:\\a\\thing.xyz"), ".xyz")

    def test_a_file_without_an_extension_is_grouped(self) -> None:
        self.assertEqual(bd.classify("C:\\a\\README"), bd.NO_EXTENSION)

    def test_a_dotfile_counts_as_no_extension(self) -> None:
        self.assertEqual(bd.classify("C:\\a\\.gitignore"), bd.NO_EXTENSION)

    def test_a_very_long_suffix_is_treated_as_part_of_the_name(self) -> None:
        self.assertEqual(bd.classify("C:\\a\\name.notarealextension"), bd.NO_EXTENSION)

    def test_a_forward_slash_path_is_handled(self) -> None:
        self.assertEqual(bd.classify("C:/a/film.mp4"), "video")

    def test_a_dot_in_a_folder_name_does_not_confuse_it(self) -> None:
        self.assertEqual(bd.classify("C:\\my.folder\\README"), bd.NO_EXTENSION)


class BreakdownTests(unittest.TestCase):
    """Groups are ordered by size and the total accounts for every file."""

    def test_groups_are_largest_first(self) -> None:
        data = bd.breakdown([_file("a.mp4", 100), _file("b.zip", 300), _file("c.py", 200)])
        self.assertEqual([group.label for group in data.groups], ["archive", "code", "video"])

    def test_sizes_and_counts_are_summed_per_kind(self) -> None:
        data = bd.breakdown([_file("a.mp4", 100), _file("b.mkv", 250)])
        self.assertEqual(data.groups[0].size, 350)
        self.assertEqual(data.groups[0].count, 2)

    def test_the_total_matches_the_input(self) -> None:
        files = [_file("a.mp4", 10), _file("b.zip", 20), _file("c", 30)]
        self.assertEqual(bd.breakdown(files).total, 60)

    def test_an_empty_input_gives_no_groups(self) -> None:
        data = bd.breakdown([])
        self.assertEqual(data.groups, [])
        self.assertEqual(data.total, 0)
        self.assertIsNone(data.biggest)

    def test_negative_sizes_are_clamped(self) -> None:
        # A malformed entry must not produce a negative segment.
        data = bd.breakdown([_file("a.mp4", -100), _file("b.zip", 50)])
        self.assertEqual(data.total, 50)

    def test_the_limit_folds_the_tail_into_other(self) -> None:
        files = [
            _file("a.mp4", 500),
            _file("b.zip", 400),
            _file("c.py", 300),
            _file("d.pdf", 200),
            _file("e.db", 100),
        ]
        data = bd.breakdown(files, limit=3)
        self.assertEqual(len(data.groups), 3)
        self.assertEqual(data.groups[-1].label, "other")
        self.assertEqual(data.groups[-1].size, 600)  # 300 + 200 + 100

    def test_no_limit_keeps_every_group(self) -> None:
        files = [_file(f"a{i}.mp4", 10) for i in range(3)] + [_file("b.zip", 20)]
        data = bd.breakdown(files, limit=0)
        self.assertEqual(len(data.groups), 2)

    def test_a_limit_above_the_group_count_changes_nothing(self) -> None:
        data = bd.breakdown([_file("a.mp4", 10), _file("b.zip", 20)], limit=99)
        self.assertEqual([group.label for group in data.groups], ["archive", "video"])

    def test_the_biggest_group_is_exposed(self) -> None:
        data = bd.breakdown([_file("a.mp4", 10), _file("b.zip", 90)])
        self.assertEqual(data.biggest.label, "archive")

    def test_shares_add_up_to_one(self) -> None:
        data = bd.breakdown([_file("a.mp4", 25), _file("b.zip", 75)])
        total = sum(data.share_of.values())
        self.assertAlmostEqual(total, 1.0)

    def test_shares_are_zero_for_an_empty_breakdown(self) -> None:
        self.assertEqual(bd.breakdown([]).share_of, {})

    def test_ties_are_broken_alphabetically(self) -> None:
        data = bd.breakdown([_file("a.mp4", 100), _file("b.zip", 100)])
        self.assertEqual([group.label for group in data.groups], ["archive", "video"])


class SegmentsTests(unittest.TestCase):
    """The SegmentBar triples carry every group, cycling the colours."""

    def test_every_group_becomes_a_segment(self) -> None:
        data = bd.breakdown([_file("a.mp4", 10), _file("b.zip", 20)])
        made = bd.segments(data, ["#111111", "#222222"])
        self.assertEqual(len(made), 2)
        self.assertEqual(made[0][0], "archive")

    def test_colours_cycle_when_there_are_more_groups(self) -> None:
        data = bd.breakdown([_file("a.mp4", 10), _file("b.zip", 20), _file("c.py", 5)])
        made = bd.segments(data, ["#111111"])
        self.assertEqual({piece[2] for piece in made}, {"#111111"})

    def test_no_colours_falls_back_to_grey(self) -> None:
        data = bd.breakdown([_file("a.mp4", 10)])
        self.assertEqual(bd.segments(data, [])[0][2], "#888888")


class SummaryTests(unittest.TestCase):
    """The caption names the biggest kind and its share."""

    def test_the_biggest_kind_and_share_are_named(self) -> None:
        data = bd.breakdown([_file("a.mp4", 25), _file("b.zip", 75)])
        line = bd.summary_line(data)
        self.assertIn("archive", line)
        self.assertIn("75%", line)

    def test_an_empty_breakdown_says_so(self) -> None:
        self.assertEqual(bd.summary_line(bd.breakdown([])), "No files to break down.")


if __name__ == "__main__":
    unittest.main()
