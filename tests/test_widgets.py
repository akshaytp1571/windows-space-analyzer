"""Tests for the presentation widgets: panels, readouts, gauges, the rail and empty states."""

from __future__ import annotations

import importlib
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _pyside_available() -> bool:
    """Return True when the PySide6 widgets module can be imported."""
    try:
        importlib.import_module("PySide6.QtWidgets")
    except (ImportError, OSError):
        return False
    return True


@unittest.skipUnless(_pyside_available(), "PySide6 is not installed")
class WidgetTests(unittest.TestCase):
    """Builds each widget and checks the state it exposes."""

    app = None

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(["windows-space-analyzer-tests"])

    # ---------------------------------------------------------------- readouts

    def test_readout_shows_and_replaces_its_value_unit_and_hint(self) -> None:
        from space_analyzer.ui.widgets import Readout

        read = Readout("Used", "1.0", "GB", "half the drive")
        self.assertEqual(read.value_text(), "1.0")
        read.set_value("2.0", "GB", "nearly full")
        self.assertEqual(read.value_text(), "2.0")
        self.assertEqual(read.unit_text(), "GB")

    def test_readout_without_a_unit_reports_an_empty_unit(self) -> None:
        from space_analyzer.ui.widgets import Readout

        read = Readout("Free")
        self.assertEqual(read.value_text(), "—")
        self.assertEqual(read.unit_text(), "")
        read.set_value("10", "GB")
        self.assertEqual(read.unit_text(), "GB")

    def test_readout_colour_is_passed_to_the_painted_figure(self) -> None:
        from space_analyzer.ui.widgets import Readout

        read = Readout("Used", "99", "%", color="#ffffff")
        read.set_color("#f87171")
        self.assertEqual(read._color, "#f87171")
        self.assertEqual(read.value_label()._color, "#f87171")

    def test_readout_number_shrinks_so_it_never_overlaps_the_unit(self) -> None:
        from PySide6.QtGui import QFontMetricsF

        from space_analyzer.ui.widgets import Readout

        read = Readout("Capacity", "231.9", "GB")
        figure = read.value_label()
        # A narrow slot: the number must give way, not run into the unit.
        figure.resize(110, 36)
        value_font, unit_font = figure._fitted_fonts()
        number = QFontMetricsF(value_font).horizontalAdvance("231.9")
        suffix = QFontMetricsF(unit_font).horizontalAdvance("GB")
        self.assertLess(value_font.pointSizeF(), Readout._VALUE_POINT)
        self.assertLessEqual(number + 5.0 + suffix, 110.0)

    def test_readout_keeps_full_size_when_the_slot_is_wide(self) -> None:
        from space_analyzer.ui.widgets import Readout

        read = Readout("Capacity", "231.9", "GB")
        figure = read.value_label()
        figure.resize(600, 36)
        value_font, _unit_font = figure._fitted_fonts()
        self.assertEqual(value_font.pointSizeF(), Readout._VALUE_POINT)

    # ------------------------------------------------------------------ gauges

    def test_tick_gauge_clamps_the_percent(self) -> None:
        from space_analyzer.ui.widgets import TickGauge

        gauge = TickGauge()
        gauge.set_values(140, caption="c:", reading="140% used")
        self.assertEqual(gauge._percent, 100.0)
        gauge.set_values(-5)
        self.assertEqual(gauge._percent, 0.0)

    def test_tick_gauge_colour_follows_the_usage_thresholds(self) -> None:
        from space_analyzer.ui import theme
        from space_analyzer.ui.widgets import TickGauge

        gauge = TickGauge()
        gauge.set_values(10)
        self.assertEqual(gauge._color, theme.usage_color(10))
        gauge.set_values(95)
        self.assertEqual(gauge._color, theme.usage_color(95))

    def test_tick_gauge_paints_without_error(self) -> None:
        from PySide6.QtGui import QPixmap

        from space_analyzer.ui.widgets import TickGauge

        gauge = TickGauge()
        gauge.resize(320, 58)
        gauge.set_values(91, caption="C: utilisation", reading="91% used")
        canvas = QPixmap(320, 58)
        gauge.render(canvas)

    def test_segment_bar_accepts_segments_and_ignores_negatives(self) -> None:
        from space_analyzer.ui.widgets import SegmentBar

        bar = SegmentBar()
        bar.set_segments([("a", 100, "#fff"), ("b", -50, "#000")])
        self.assertEqual([value for _l, value, _c in bar._segments], [100.0, 0.0])

    def test_legend_row_replaces_its_value(self) -> None:
        from space_analyzer.ui.widgets import LegendRow

        row = LegendRow("Free space", "1 GB", "#123456")
        row.set_value("2 GB")
        self.assertEqual(row._value.text(), "2 GB")

    # ------------------------------------------------------------------- rail

    def test_rail_lists_pages_with_icons_and_reports_the_current_one(self) -> None:
        from space_analyzer.ui.widgets import RailNav

        rail = RailNav()
        for title, glyph in (
            ("Overview", "gauge"),
            ("Suggestions", "spark"),
            ("Large files", "file"),
            ("Duplicates", "copy"),
        ):
            rail.add_page(title, glyph, f"hint for {title}")
        nav = rail.nav_widget()
        self.assertEqual(nav.objectName(), "navigation")
        self.assertEqual(
            [nav.item(i).text() for i in range(nav.count())],
            ["Overview", "Suggestions", "Large files", "Duplicates"],
        )
        self.assertEqual(nav.item(0).toolTip(), "hint for Overview")
        self.assertFalse(nav.item(0).icon().isNull())
        rail.set_current(2)
        self.assertEqual(rail.current(), 2)

    def test_rail_emits_the_page_index_when_it_changes(self) -> None:
        from space_analyzer.ui.widgets import RailNav

        rail = RailNav()
        rail.add_page("A", "gauge")
        rail.add_page("B", "file")
        seen: list[int] = []
        rail.current_changed.connect(seen.append)
        rail.set_current(1)
        self.assertEqual(seen, [1])

    # ------------------------------------------------------------ empty state

    def test_empty_state_replaces_both_lines(self) -> None:
        from space_analyzer.ui.widgets import EmptyState

        state = EmptyState("NO SCAN YET", "Click scan")
        state.set_text("NO MATCH", "Try a smaller size")
        self.assertEqual(state._title.text(), "NO MATCH")
        self.assertEqual(state._hint.text(), "Try a smaller size")

    # ----------------------------------------------------------------- panels

    def test_panel_holds_widgets_and_a_header_with_actions(self) -> None:
        from PySide6.QtWidgets import QLabel, QPushButton

        from space_analyzer.ui.widgets import Panel

        panel = Panel(title="Folders", glyph="folder", caption="largest first")
        header = panel.header()
        button = header.add_action(QPushButton("Open"))
        label = panel.add(QLabel("body"))
        self.assertIsNotNone(header)
        self.assertEqual(header._caption.text(), "largest first")
        # The action button lives inside the header, the body widget inside the panel.
        self.assertIs(button.parent(), header)
        self.assertIs(label.parent(), panel.body().parentWidget())

    def test_panel_without_a_title_has_no_header(self) -> None:
        from space_analyzer.ui.widgets import Panel

        self.assertIsNone(Panel().header())

    def test_panel_header_caption_can_be_cleared(self) -> None:
        from space_analyzer.ui.widgets import Panel

        panel = Panel(title="T", caption="caption")
        header = panel.header()
        header.set_caption("")
        self.assertFalse(header._caption.isVisible())

    # ------------------------------------------------------------------ icons

    def test_every_icon_name_paints_a_non_empty_pixmap(self) -> None:
        from space_analyzer.ui import icons

        self.assertGreaterEqual(len(icons.names()), 16)
        for name in icons.names():
            with self.subTest(icon=name):
                image = icons.pixmap(name, 18, "#ffffff")
                self.assertFalse(image.isNull())

    def test_unknown_icon_returns_a_blank_pixmap_instead_of_raising(self) -> None:
        from space_analyzer.ui import icons

        blank = icons.pixmap("no-such-icon", 18)
        self.assertFalse(blank.isNull())
        # The icon object is still usable, it simply paints nothing.
        self.assertFalse(icons.icon("no-such-icon").pixmap(18, 18).isNull())

    # ------------------------------------------------------------------ theme

    def test_marking_a_button_applies_the_named_property(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from space_analyzer.ui.theme import mark_danger, mark_ghost, mark_primary

        for marker, prop in (
            (mark_primary, "primary"),
            (mark_ghost, "ghost"),
            (mark_danger, "danger"),
        ):
            with self.subTest(prop=prop):
                button = QPushButton("x")
                marker(button)
                self.assertEqual(button.property(prop), "true")

    def test_risk_colours_cover_every_risk(self) -> None:
        from space_analyzer.models import Risk
        from space_analyzer.ui.theme import RISK_COLOR, RISK_LABEL

        for risk in Risk:
            with self.subTest(risk=risk):
                self.assertIn(risk, RISK_LABEL)
                self.assertIn(risk, RISK_COLOR)
                self.assertTrue(RISK_COLOR[risk].startswith("#"))

    def test_mono_font_asks_for_a_monospace_family(self) -> None:
        from PySide6.QtGui import QFont

        from space_analyzer.ui.theme import mono_font

        self.assertEqual(mono_font().styleHint(), QFont.StyleHint.Monospace)
        self.assertTrue(mono_font(9, bold=True).bold())

    def test_theme_applies_without_error(self) -> None:
        from space_analyzer.ui.theme import apply_theme

        apply_theme(self.app)
        self.assertTrue(self.app.styleSheet())

    # --------------------------------------------------------------- helpers

    def test_split_bytes_separates_number_and_unit(self) -> None:
        from space_analyzer.ui.widgets import split_bytes

        self.assertEqual(split_bytes(1024 * 1024 * 1024), ("1.0", "GB"))
        self.assertEqual(split_bytes(None), ("—", ""))

    def test_split_percent_separates_number_and_sign(self) -> None:
        from space_analyzer.ui.widgets import split_percent

        self.assertEqual(split_percent(91.4), ("91", "%"))


if __name__ == "__main__":
    unittest.main()
