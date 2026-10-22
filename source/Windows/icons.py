"""Build Windows assets from the app's own App/Icon.swift vector artwork.

Qt paints the existing geometry; ICO contains standard PNG-backed sizes. No
external images, generators or extra runtime dependencies are required.
"""
from pathlib import Path
import struct

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def asset_path(name):
    # PyInstaller sets __file__ inside _internal, where --add-data puts assets.
    return Path(__file__).resolve().parent / "assets" / name


def notification_icon_path():
    path = asset_path("app.png")
    return path if path.is_file() else None


def render_icon(size):
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 1024, size / 1024)
    gradient = QLinearGradient(80, 80, 944, 944)
    gradient.setColorAt(0, QColor.fromRgbF(0.18, 0.62, 0.98))
    gradient.setColorAt(1, QColor.fromRgbF(0.24, 0.30, 0.76))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(80, 80, 864, 864), 195, 195)
    # AppKit uses bottom-left coordinates; Qt uses top-left coordinates.
    plane = QPainterPath(QPointF(250, 484))
    plane.lineTo(783, 258)
    plane.cubicTo(802, 249, 810, 258, 801, 282)
    plane.lineTo(707, 712)
    plane.cubicTo(703, 735, 686, 737, 671, 724)
    for x, y in ((526, 613), (455, 682), (463, 560), (700, 345), (409, 522), (263, 475)):
        plane.lineTo(x, y)
    plane.closeSubpath()
    painter.setBrush(Qt.GlobalColor.white)
    painter.drawPath(plane)
    painter.setBrush(QColor.fromRgbF(0.23, 0.77, 0.65))
    painter.drawEllipse(QRectF(666, 666, 220, 220))
    pen = QPen(Qt.GlobalColor.white, 19, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    check = QPainterPath(QPointF(725, 777))
    check.lineTo(761, 813)
    check.lineTo(825, 742)
    painter.drawPath(check)
    painter.end()
    return image


def application_icon():
    from PySide6.QtGui import QIcon, QPixmap
    icon = QIcon(str(asset_path("app.ico")))
    return icon if not icon.isNull() else QIcon(QPixmap.fromImage(render_icon(128)))


def write_assets(directory):
    from PySide6.QtCore import QBuffer, QIODevice
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not render_icon(512).save(str(directory / "app.png"), "PNG"):
        raise RuntimeError("Could not write application PNG")
    payloads = []
    for size in SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not render_icon(size).save(buffer, "PNG"):
            raise RuntimeError("Could not encode application icon")
        payloads.append(bytes(buffer.data()))
        buffer.close()
    offset = 6 + 16 * len(SIZES)
    entries = []
    for size, payload in zip(SIZES, payloads):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    (directory / "app.ico").write_bytes(struct.pack("<HHH", 0, 1, len(SIZES)) + b"".join(entries) + b"".join(payloads))


def verify_executable_icon(executable):
    """Verify final PE icon resources match our ICO, rather than a Python icon."""
    import ctypes
    source = asset_path("app.ico").read_bytes()
    if struct.unpack_from("<HHH", source) != (0, 1, len(SIZES)):
        raise RuntimeError("Application ICO directory mismatch")
    kernel = ctypes.WinDLL("kernel32")
    signatures = {
        "LoadLibraryExW": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_void_p),
        "FindResourceW": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
        "SizeofResource": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_uint32),
        "LoadResource": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
        "LockResource": ([ctypes.c_void_p], ctypes.c_void_p),
        "FreeLibrary": ([ctypes.c_void_p], ctypes.c_int32),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    module = kernel.LoadLibraryExW(str(executable), None, 0x22)  # Data/image resource, no code execution.
    if not module:
        raise RuntimeError("Cannot inspect executable icon resources")
    try:
        def resource(identifier, kind):
            handle = kernel.FindResourceW(module, ctypes.c_void_p(identifier), ctypes.c_void_p(kind))
            if not handle:
                raise RuntimeError("Executable icon resource missing")
            size = kernel.SizeofResource(module, handle)
            pointer = kernel.LockResource(kernel.LoadResource(module, handle))
            if not pointer or not 0 < size <= 1_048_576:
                raise RuntimeError("Invalid executable icon resource")
            return ctypes.string_at(pointer, size)

        group = resource(1, 14)  # RT_GROUP_ICON
        if struct.unpack_from("<HHH", group) != (0, 1, len(SIZES)):
            raise RuntimeError("Executable icon resolutions mismatch")
        for index in range(len(SIZES)):
            expected = struct.unpack_from("<BBBBHHII", source, 6 + index * 16)
            actual = struct.unpack_from("<BBBBHHIH", group, 6 + index * 14)
            if expected[:7] != actual[:7]:
                raise RuntimeError("Executable icon metadata mismatch")
            payload = source[expected[7]:expected[7] + expected[6]]
            if resource(actual[7], 3) != payload:  # RT_ICON
                raise RuntimeError("Executable still contains a different application icon")
    finally:
        kernel.FreeLibrary(module)


if __name__ == "__main__":
    import sys
    write_assets(sys.argv[1])
