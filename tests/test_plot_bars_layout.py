"""plot_results.plot_bars keeps long bar labels inside the figure (offline, no Engine run)."""

import unittest

from tests.workflow_fixtures import TempDir, quiet_environment

quiet_environment()

from examples import plot_results as pr  # noqa: E402


class PlotBarsLayoutTests(unittest.TestCase):
    def test_long_labels_stay_inside_the_figure(self):
        captured = []
        original = pr.save

        def capture(fig, out_dir, name, text):
            captured.append(fig)
            return original(fig, out_dir, name, text)

        pr.save = capture
        try:
            with TempDir() as tmp:
                pr.plot_bars(["a_rather_long_parameter_name\nscale 0.05 mm", "short"], [1.0, -0.5], "mm", tmp, "bars",
                             title="labels")
        finally:
            pr.save = original
        fig = captured[0]
        # A saved/closed figure can have the base canvas after an SVG export.
        # Render the final layout explicitly before checking its label bounds.
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        for label in fig.axes[0].get_yticklabels():
            self.assertGreaterEqual(label.get_window_extent(renderer).x0, 0.0)


if __name__ == "__main__":
    unittest.main()
