"""Tests for the MarkerRegistry auto-discovery and metadata."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.analysis.markers import MarkerRegistry


class TestMarkerRegistry:
    def setup_method(self):
        # Reset cached markers so discovery runs fresh
        MarkerRegistry._markers = None
        self.registry = MarkerRegistry()

    def test_discovers_all_markers(self):
        markers = self.registry.get_all()
        names = [m.name() for m in markers]
        # Must include the 5 chart markers + 3 screener-only
        assert 'coil' in names
        assert 'ignition' in names
        assert 'spring' in names
        assert 'grind' in names
        assert 'intensity' in names
        assert 'crossover_up' in names
        assert 'crossover_down' in names
        assert 'high_score' in names
        assert len(markers) == 12

    def test_unique_names(self):
        markers = self.registry.get_all()
        names = [m.name() for m in markers]
        assert len(names) == len(set(names)), f"Duplicate names found: {names}"

    def test_metadata_returns_all(self):
        metas = self.registry.get_metadata()
        assert len(metas) == 12
        for meta in metas:
            assert 'id' in meta
            assert 'label' in meta
            assert 'is_chart_marker' in meta

    def test_metadata_chart_markers_have_required_keys(self):
        metas = self.registry.get_metadata()
        chart_metas = [m for m in metas if m['is_chart_marker']]
        assert len(chart_metas) >= 4  # coil, ignition, spring, grind

        for meta in chart_metas:
            assert 'color' in meta, f"{meta['id']} missing color"
            assert 'shape' in meta, f"{meta['id']} missing shape"
            assert 'position' in meta, f"{meta['id']} missing position"
            assert 'flag_key' in meta, f"{meta['id']} missing flag_key"
            assert 'help_title' in meta, f"{meta['id']} missing help_title"
            assert 'help_html' in meta, f"{meta['id']} missing help_html"
            assert 'legend_dot_style' in meta, f"{meta['id']} missing legend_dot_style"

    def test_screener_names(self):
        names = self.registry.get_screener_names()
        # Should include chart marker screeners + screener-only markers
        assert 'SCR: Coil' in names
        assert 'SCR: Ignition' in names
        assert 'SCR: Spring' in names
        assert 'SCR: Grind' in names
        assert 'SCR: CO-U' in names
        assert 'SCR: CO-D' in names
        assert 'SCR: 90UP' in names
        # Intensity has no screener
        assert len([n for n in names if 'Intensity' in n]) == 0

    def test_evaluation_order(self):
        markers = self.registry.get_all()
        orders = [getattr(m, '_order', 50) for m in markers]
        assert orders == sorted(orders), "Markers not in evaluation order"
