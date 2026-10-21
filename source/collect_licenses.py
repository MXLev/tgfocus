import importlib.metadata
from pathlib import Path
import shutil
import sys
import sysconfig

destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
for package in ["Telethon", "pyaes", "rsa", "pyasn1", "pyinstaller", "certifi"]:
    distribution = importlib.metadata.distribution(package)
    target = destination / package
    target.mkdir(exist_ok=True)
    metadata = distribution.read_text("METADATA") or distribution.read_text("PKG-INFO") or package
    (target / "METADATA.txt").write_text(metadata, encoding="utf-8")
    for file in distribution.files or []:
        if any(marker in file.name.upper() for marker in ["LICENSE", "COPYING", "COPYRIGHT"]):
            source = Path(distribution.locate_file(file))
            if source.is_file(): shutil.copy2(source, target / file.name)
python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
if python_license.is_file(): shutil.copy2(python_license, destination / "Python-LICENSE.txt")
