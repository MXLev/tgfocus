# Qt / PySide6 / Shiboken notices and source

TGFocus for Windows uses unmodified upstream **PySide6 Essentials 6.12.0**,
**Shiboken6 6.12.0** and the included **Qt 6.12.0** shared libraries. Copyright
The Qt Company Ltd. and the respective contributors; third-party copyright
holders and notices are reproduced in the upstream attribution files below.

The selected QtCore, QtGui, QtWidgets and related Qtbase plugins are used under
the **GNU Lesser General Public License version 3** option. The LGPLv3 text and
its incorporated GPLv3 text are included under `qtbase/LICENSES/` and
`pyside/LICENSES/`. Additional upstream license texts cover third-party code;
their presence does not mean every listed optional component is compiled into
this particular wheel. TGFocus excludes QtPdf/QtWebEngine and optional
PDF/SVG/WebP/TIFF image plugins from this build.

`qtbase/src/3rdparty/` and `pyside/sources/shiboken6/` preserve the upstream
copyright/attribution records and their referenced license files. This is a
conservative notice set for Qtbase/Core/Gui/Widgets and Windows plugins, not an
assertion that unused platform-specific code is distributed. `UPSTREAM.json`
records each included file's release-tag source URL and SHA-256 digest. These
files were retrieved from official Qt project repositories at tag `v6.12.0`;
the build copies the committed files without downloading notices at runtime.

## Corresponding upstream source

- PySide6 / Shiboken6 source, tag `v6.12.0`:
  https://github.com/pyside/pyside-setup/tree/v6.12.0
- PySide6 / Shiboken6 source archive:
  https://github.com/pyside/pyside-setup/archive/refs/tags/v6.12.0.tar.gz
- Qtbase (QtCore, QtGui, QtWidgets, Windows platform and ordinary image plugins),
  tag `v6.12.0`: https://github.com/qt/qtbase/tree/v6.12.0
- Qtbase source archive:
  https://github.com/qt/qtbase/archive/refs/tags/v6.12.0.tar.gz
- Qt source and release archives:
  https://download.qt.io/archive/qt/6.12/6.12.0/
- Qt for Python licensing:
  https://doc.qt.io/qtforpython-6/licenses.html
- Exact PyPI wheel distributions used by the build:
  https://pypi.org/project/PySide6-Essentials/6.12.0/
  https://pypi.org/project/shiboken6/6.12.0/

Full Qt/PySide source archives are **not** included in this portable package.
The `Source/` folder contains TGFocus's corresponding Windows/helper source and
build files; the complete TGFocus repository is https://github.com/MXLev/tgfocus .
The application's own code is MIT licensed; this does not replace or restrict
the LGPL and third-party licenses applying to its dependencies.

## Replacing and modifying the libraries

This is an ordinary PyInstaller **onedir** distribution, not a sealed single
executable. Qt DLLs, PySide6 bindings and Shiboken are in `_internal/` (typically
`_internal/PySide6` and `_internal/shiboken6`). You may replace them with
compatible modified versions. No TGFocus license restriction prohibits reverse
engineering needed to debug modifications to these LGPL components.

For changes involving packaged Python support code, rebuild the open application
using `Source/source/Windows/build.py`, Python 3.13 x64 and a build virtual environment
containing your modified compatible PySide6-Essentials/Shiboken6 wheels. Preserve
the `6.12.0` package version or adjust the included requirements to your build,
then pass `--env PATH_TO_VENV`. PySide/Qt's upstream source above provides their
build instructions. The script does not force-reinstall an already installed
matching wheel. Keep this notice and the applicable licenses with redistributed
copies and follow the LGPL requirements for your modifications.
