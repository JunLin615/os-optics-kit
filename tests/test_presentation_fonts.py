"""Font discovery for presentation figures (os_optics_kit.presentation): which installed families draw Chinese.

The discovery is tested with substituted font lists, so it runs without the fonts of any one system; the last
tests use matplotlib and the fonts actually installed (they skip without matplotlib or, for Chinese, without a
Chinese font unless OS_OPTICS_KIT_REQUIRE_CJK_FONT=1 or OS_OPTICS_KIT_REQUIRE_PRESENTATION=1).
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from os_optics_kit import presentation as pr
from tests.font_fixtures import require_chinese_font

HAS_MATPLOTLIB = find_spec("matplotlib") is not None


def fonts(index, chinese):
    """Patches that make ``index`` the installed families and the faces in ``chinese`` the ones drawing Chinese."""
    return [mock.patch.dict(pr._FONT_CACHE, clear=True),
            mock.patch.object(pr, "_font_index", return_value=index),
            mock.patch.object(pr, "_draws", side_effect=lambda face, text: face in chinese),
            mock.patch.object(pr, "_charmap", side_effect=lambda face: (
                set(range(32, 127)) | ({ord(c) for c in pr.CJK_PROBE + "中文"} if face in chinese else set())))]


class FontPatches(unittest.TestCase):
    def use(self, index, chinese=()):
        for patch in fonts(index, set(chinese)):
            patch.start()
            self.addCleanup(patch.stop)


class ChineseFamilyTests(FontPatches):
    def test_known_families_come_first_in_list_order(self):
        self.use({"DejaVu Sans": ("dejavu.ttf", 0), "Arial": ("arial.ttf", 0), "Songti SC": ("songti.ttc", 0),
                  "Hiragino Sans GB": ("hiragino.ttc", 0), "Some Chinese": ("other.ttf", 0)},
                 chinese={("songti.ttc", 0), ("hiragino.ttc", 0), ("other.ttf", 0)})
        self.assertEqual(pr.cjk_families(), ["Hiragino Sans GB", "Songti SC"])
        self.assertTrue(pr.has_cjk_font())
        self.assertEqual(pr.installed_families([], "zh-CN"), ["DejaVu Sans", "Arial", "Hiragino Sans GB", "Songti SC"])
        theme = pr.resolve_theme({"language": "zh-CN"})
        self.assertEqual(pr.missing_glyphs(["中文标题"], theme.font_family), [])

    def test_macos_and_windows_system_families_are_known(self):
        for family in ("PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti", "Songti SC", "Arial Unicode MS",
                       "Microsoft YaHei", "SimHei", "DengXian"):
            self.assertIn(family, pr.CJK_FONTS)

    def test_a_listed_family_without_chinese_glyphs_is_not_used(self):
        self.use({"DejaVu Sans": ("dejavu.ttf", 0), "SimHei": ("broken.ttf", 0)})
        self.assertEqual(pr.cjk_families(), [])

    def test_an_unlisted_chinese_font_is_found_by_its_glyphs(self):
        # one name per face (matplotlib also lists a font under alternative names), at most three, by name
        self.use({"DejaVu Sans": ("dejavu.ttf", 0), "Zeta Hei": ("zeta.ttc", 0), "Zeta Hei W3": ("zeta.ttc", 0),
                  "Beta Song": ("beta.ttf", 0), "Alpha Kai": ("alpha.ttf", 0), "Gamma Ming": ("gamma.ttf", 0),
                  "Latin Serif": ("serif.ttf", 0)},
                 chinese={("zeta.ttc", 0), ("beta.ttf", 0), ("alpha.ttf", 0), ("gamma.ttf", 0)})
        self.assertEqual(pr.cjk_families(), ["Alpha Kai", "Beta Song", "Gamma Ming"])
        self.assertEqual(pr.installed_families(["Latin Serif"], "zh-CN"),
                         ["Latin Serif", "DejaVu Sans", "Alpha Kai", "Beta Song", "Gamma Ming"])

    def test_without_a_chinese_font_the_text_is_refused(self):
        self.use({"DejaVu Sans": ("dejavu.ttf", 0), "Arial": ("arial.ttf", 0)})
        self.assertFalse(pr.has_cjk_font())
        theme = pr.resolve_theme({"language": "zh-CN"})
        self.assertEqual(theme.font_family, ["DejaVu Sans", "Arial"])
        with self.assertRaises(pr.PresentationError) as caught:
            pr.check_glyphs(["标题"], theme)
        self.assertEqual(caught.exception.code, "FONT_MISSING")
        self.assertIn("Noto Sans CJK SC", caught.exception.details["recovery"])

    def test_without_matplotlib_the_requested_and_default_names_are_kept(self):
        with mock.patch.dict(pr._FONT_CACHE, clear=True), \
                mock.patch.object(pr, "_font_index", side_effect=ImportError("matplotlib")):
            families = pr.installed_families(["Custom"], "zh-CN")
        self.assertEqual(families, list(dict.fromkeys(["Custom", *pr.LATIN_FONTS, *pr.CJK_FONTS])))


@unittest.skipUnless(HAS_MATPLOTLIB, 'needs "os-optics-kit[plot]"')
class FontIndexTests(unittest.TestCase):
    def index(self, entries):
        from matplotlib import font_manager

        with mock.patch.dict(pr._FONT_CACHE, clear=True), \
                mock.patch.object(pr, "_register_macos_fonts"), \
                mock.patch.object(font_manager.fontManager, "ttflist", entries):
            return pr._font_index()

    @staticmethod
    def entry(name, fname, weight, style="normal", index=0):
        return SimpleNamespace(name=name, fname=fname, weight=weight, style=style, index=index)

    def test_regular_faces_first_then_light_faces_never_thin_bold_or_italic(self):
        index = self.index([
            self.entry("Regular Sans", "regular.ttf", 400),
            self.entry("Named Weight", "named.ttf", "normal"),
            self.entry("Hiragino Sans GB", "hiragino.ttc", 300),  # W3: the text weight of the family
            self.entry("Hiragino Sans GB", "hiragino.ttc", 600, index=1),
            self.entry("Both", "both-light.ttf", 300),
            self.entry("Both", "both-regular.ttc", 400, index=2),
            self.entry("Noto Sans SC", "NotoSansSC-VF.ttf", 100),  # a variable font registered as Thin
            self.entry("Bold Only", "bold.ttf", 700),
            self.entry("Italic Only", "italic.ttf", 400, style="italic"),
        ])
        self.assertEqual(index, {"Regular Sans": ("regular.ttf", 0), "Named Weight": ("named.ttf", 0),
                                 "Hiragino Sans GB": ("hiragino.ttc", 0), "Both": ("both-regular.ttc", 2)})

    def test_entries_without_a_face_index_read_the_first_face(self):
        entry = SimpleNamespace(name="Old", fname="old.ttf", weight=400, style="normal")  # matplotlib < 3.11
        self.assertEqual(self.index([entry]), {"Old": ("old.ttf", 0)})

    def test_faces_of_a_font_file_are_read(self):
        from matplotlib import get_data_path

        dejavu = (str(Path(get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"), 0)
        self.assertTrue(pr._draws(dejavu, "Ab µ"))
        self.assertFalse(pr._draws(dejavu, "中"))
        self.assertFalse(pr._draws(("no-such-font.ttf", 0), "A"))
        with mock.patch.dict(pr._FONT_CACHE, clear=True):
            self.assertIn(ord("A"), pr._charmap(dejavu))
            self.assertEqual(pr._charmap(("no-such-font.ttf", 0)), set())

    def test_a_placeholder_font_does_not_count_as_drawing_chinese(self):
        from matplotlib import get_data_path

        # matplotlib >= 3.11 bundles a font that maps every character of a Unicode block to one placeholder box
        last_resort = Path(get_data_path()) / "fonts" / "ttf" / "LastResortHE-Regular.ttf"
        if not last_resort.is_file():
            self.skipTest("this matplotlib does not bundle Last Resort High-Efficiency")
        self.assertFalse(pr._draws((str(last_resort), 0), pr.CJK_PROBE))
        with mock.patch.dict(pr._FONT_CACHE, clear=True), \
                mock.patch.object(pr, "_font_index", return_value={"DejaVu Sans": ("missing.ttf", 0),
                                                                     "Last Resort High-Efficiency": (str(last_resort),
                                                                                                     0)}):
            self.assertEqual(pr.cjk_families(), [])


class MacFontAssetTests(unittest.TestCase):
    def test_chinese_system_fonts_in_asset_folders_are_registered_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {"com_apple_MobileAsset_Font7/3419f2a4.asset/AssetData/PingFang.ttc": True,
                     "com_apple_MobileAsset_Font7/5c2d.asset/AssetData/Lantinghei.ttc": True,
                     "com_apple_MobileAsset_Font8/77aa.asset/AssetData/Songti.ttc": False,  # already listed
                     "com_apple_MobileAsset_Font7/9b1e.asset/AssetData/Farah.ttc": False,  # not a Chinese font
                     "com_apple_MobileAsset_Font7/3419f2a4.asset/AssetData/PingFang.plist": False,
                     "com_apple_MobileAsset_Other/1.asset/AssetData/PingFang.ttc": False}
            for name in files:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(b"")
            added = []
            listed = [SimpleNamespace(fname=str(root / "com_apple_MobileAsset_Font8/77aa.asset/AssetData/Songti.ttc"))]
            manager = SimpleNamespace(fontManager=SimpleNamespace(ttflist=listed, addfont=added.append))
            with mock.patch.object(pr, "MACOS_FONT_ASSETS", str(root)):
                pr._register_macos_fonts(manager)
            self.assertEqual(sorted(added), sorted(root / name for name, expected in files.items() if expected))
            with mock.patch.object(pr, "MACOS_FONT_ASSETS", str(root / "missing")):
                pr._register_macos_fonts(manager)  # no asset folder (Linux, Windows, older macOS): nothing added
            self.assertEqual(len(added), 2)

    def test_an_unreadable_asset_font_is_left_out(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "com_apple_MobileAsset_Font7" / "a.asset" / "AssetData" / "PingFang.ttc"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not a font")
            manager = SimpleNamespace(fontManager=SimpleNamespace(ttflist=[], addfont=mock.Mock(
                side_effect=RuntimeError("Can not load face"))))
            with mock.patch.object(pr, "MACOS_FONT_ASSETS", directory):
                pr._register_macos_fonts(manager)
            manager.fontManager.addfont.assert_called_once_with(path)


@unittest.skipUnless(HAS_MATPLOTLIB, 'needs "os-optics-kit[plot]"')
class InstalledFontTests(unittest.TestCase):
    def test_the_installed_chinese_font_draws_the_theme_text(self):
        require_chinese_font(self)
        theme = pr.resolve_theme({"language": "zh-CN"})
        self.assertTrue(set(pr.cjk_families()) <= set(theme.font_family), theme.font_family)
        self.assertEqual(pr.missing_glyphs([pr.CJK_PROBE, "目标值调整前后缺失"], theme.font_family), [])
        pr.check_glyphs([pr.text("axis.samples", "zh-CN")], theme)


if __name__ == "__main__":
    unittest.main()
