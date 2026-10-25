"""Keep this tray application's image plugins within the Qtbase LGPL component set."""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# Qt's Essentials wheel also includes optional PDF/SVG/WebP/TIFF plugins. This
# application renders its QR in memory and only needs Qtbase's ordinary formats.
# Filter before binary dependency analysis, so unused QtPdf is never collected.
# SVG's separate icon engine also links QtSvg; QPixmap-painted tray icons use
# QtGui's built-in engine, so no external icon-engine plugin is required here.
allowed_image_plugins = {"qgif.dll", "qico.dll", "qjpeg.dll"}
binaries = [(source, destination) for source, destination in binaries
            if Path(source).parent.name != "iconengines" and
            (Path(source).parent.name != "imageformats" or Path(source).name.lower() in allowed_image_plugins)]
