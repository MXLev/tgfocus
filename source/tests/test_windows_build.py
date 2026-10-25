"""Verify optional Qt plugins cannot pull additional modules into the payload."""
from pathlib import Path
import runpy
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


class QtPackagingTests(unittest.TestCase):
    def test_qtgui_hook_excludes_svg_icon_engine_and_optional_image_plugins(self):
        root = Path("fake-qt")
        sources = [root / folder / name for folder, name in [
            ("iconengines", "qsvgicon.dll"),
            ("imageformats", "qpdf.dll"),
            ("imageformats", "qsvg.dll"),
            ("imageformats", "qwebp.dll"),
            ("imageformats", "qgif.dll"),
            ("imageformats", "qico.dll"),
            ("imageformats", "qjpeg.dll"),
            ("platforms", "qwindows.dll"),
            ("platforms", "qoffscreen.dll"),
        ]]
        modules = {name: ModuleType(name) for name in (
            "PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks", "PyInstaller.utils.hooks.qt")}
        modules["PyInstaller.utils.hooks.qt"].add_qt6_dependencies = lambda path: (
            ["PySide6.QtCore"], [(str(source), "plugins") for source in sources], [("translation", "translations")])
        with patch.dict(sys.modules, modules):
            result = runpy.run_path(str(Path(__file__).resolve().parents[1] / "Windows/hooks/hook-PySide6.QtGui.py"))
        kept = {Path(source).name for source, destination in result["binaries"]}
        self.assertEqual(kept, {"qgif.dll", "qico.dll", "qjpeg.dll", "qwindows.dll", "qoffscreen.dll"})
        self.assertEqual(result["hiddenimports"], ["PySide6.QtCore"])
        self.assertEqual(result["datas"], [("translation", "translations")])


if __name__ == "__main__":
    unittest.main()
