"""Collect installed license files for the local portable build."""
import importlib.metadata
import shutil
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    output = root / "dist" / "ModLingo" / "THIRD_PARTY_NOTICES"
    output.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    for candidate in [base / "LICENSE.txt", base / "LICENSE_PYTHON.txt"]:
        if candidate.is_file():
            shutil.copyfile(candidate, output / "CPython-LICENSE.txt")
            break
    for lib in [base / "tcl" / "tcl8.6", base / "tcl" / "tk8.6"]:
        path = lib / "license.terms"
        if path.is_file():
            shutil.copyfile(path, output / (lib.name + "-license.terms"))
    for name in ["pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "packaging", "importlib-metadata", "zipp"]:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        index = 0
        for file in distribution.files or []:
            if any(word in str(file).lower() for word in ("license", "copying")) and "dist-info" in str(file):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    shutil.copyfile(source, output / (name + "-" + str(index) + "-" + source.name))
                    index += 1


if __name__ == "__main__":
    main()
