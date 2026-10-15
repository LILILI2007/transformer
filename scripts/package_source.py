"""Package only the explicit public source tree, never local build or data."""
import hashlib
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
release = root / "release"
release.mkdir(exist_ok=True)
files = [root / name for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml", "requirements-build.txt", "Start.cmd", "run_modlingo.py", ".gitignore", "artifacts/preview.png")]
for name in ("modlingo", "examples", "tests", "docs", "scripts", ".github"):
    files.extend(path for path in (root / name).rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"))
with zipfile.ZipFile(release / "ModLingo-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, "ModLingo/" + path.relative_to(root).as_posix())
with (release / "SHA256SUMS.txt").open("w", encoding="ascii") as output:
    for path in sorted(release.glob("*.zip")):
        output.write(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")
print("Source archive and SHA256SUMS created.")
