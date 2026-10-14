"""Build the portable Windows executable and release archive."""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import zipfile


def main():
    if sys.platform != "win32":
        raise SystemExit("Please build this Windows application on Windows.")
    if sys.maxsize < 2**32:
        raise SystemExit("Please use 64-bit Python to build the x64 release.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise SystemExit("Version must have the form 1.0.0.")
    repo = Path(__file__).resolve().parent
    dist = repo / "dist"
    work = repo / "build"
    dist.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    version_tuple = tuple(map(int, args.version.split("."))) + (0,)
    version_file = work / "version_info.txt"
    version_file.write_text(
        "VSVersionInfo(ffi=FixedFileInfo("
        f"filevers={version_tuple!r}, prodvers={version_tuple!r}, "
        "mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)), "
        "kids=[StringFileInfo([StringTable('040904B0', ["
        "StringStruct('FileDescription', '练习计时器'), "
        f"StringStruct('FileVersion', '{args.version}'), "
        "StringStruct('InternalName', 'PracticeTimer'), "
        "StringStruct('OriginalFilename', '练习计时器.exe'), "
        "StringStruct('ProductName', '练习计时器'), "
        f"StringStruct('ProductVersion', '{args.version}')"
        "])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])",
        encoding="utf-8",
    )
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--windowed", "--noupx", "--name", "练习计时器", "--icon", str(repo / "timer-light.ico"),
        "--add-data", f"{repo / 'timer-light.ico'}:.", "--version-file", str(version_file),
        "--distpath", str(dist), "--workpath", str(work / "pyinstaller"),
        "--specpath", str(work), str(repo / "practice_timer.py"),
    ], cwd=repo, check=True)
    executable = dist / "练习计时器.exe"
    archive = dist / f"PracticeTimer-v{args.version}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.write(executable, "练习计时器.exe")
        bundle.write(repo / "使用说明.txt", "使用说明.txt")
    checksums = dist / "SHA256SUMS.txt"
    checksum_lines = []
    for path in (executable, archive):
        with path.open("rb") as handle:
            checksum_lines.append(f"{hashlib.file_digest(handle, 'sha256').hexdigest()}  {path.name}\n")
    checksums.write_text("".join(checksum_lines), encoding="utf-8")
    print(f"Release archive: {archive}")
    print(f"Checksums: {checksums}")


if __name__ == "__main__":
    main()
