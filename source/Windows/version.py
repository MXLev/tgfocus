"""One product version for the Windows UI, both PE files and release tags."""
from pathlib import Path

VERSION = "0.1.0"
FILE_VERSION = tuple(map(int, VERSION.split("."))) + (0,)
PRODUCT_NAME = "TGFocus"


def write_version_info(destination, filename, description):
    strings = {
        "CompanyName": "MXLev",
        "FileDescription": description,
        "FileVersion": ".".join(map(str, FILE_VERSION)),
        "InternalName": Path(filename).stem,
        "OriginalFilename": filename,
        "ProductName": PRODUCT_NAME,
        "ProductVersion": VERSION,
    }
    entries = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={FILE_VERSION!r}, prodvers={FILE_VERSION!r},
    mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904b0', [{entries}])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
    Path(destination).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    print(VERSION)
