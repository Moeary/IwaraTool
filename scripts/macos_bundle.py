"""Make a Nuitka-built macOS .app self-contained, re-sign it and package it.

Nuitka can leave load commands that point back into the build machine's
Python environment (for example
``/Users/runner/work/IwaraTool/IwaraTool/.pixi/envs/default/lib/libpython3.11.dylib``).
The build machine still has that file, so the app starts there and only crashes
on a user's Mac.  This module rewrites such references to the copies inside the
bundle, drops build-machine rpaths, re-signs the bundle (``install_name_tool``
invalidates signatures, and Apple Silicon refuses to run invalid code), and
audits the result.

The parsing and planning helpers are pure functions, so they are unit tested on
every platform; only the ``run_*`` / ``*_bundle`` functions call Apple tools.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SYSTEM_PREFIXES = ("/usr/lib/", "/System/")
# Apple's tools by absolute path: the pixi build environment ships its own
# ``codesign`` (sigtool) and cctools on PATH, and that ``codesign`` rejects
# options such as ``--timestamp=none`` or ``--deep``.
CODESIGN = "/usr/bin/codesign"
OTOOL = "/usr/bin/otool"
INSTALL_NAME_TOOL = "/usr/bin/install_name_tool"
XATTR = "/usr/bin/xattr"
MACHO_MAGICS = frozenset({
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",  # 32-bit
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",  # 64-bit
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",  # universal (fat)
})


# ── otool output parsing ─────────────────────────────────────────────────────

def parse_otool_libraries(text: str) -> list[str]:
    """Dependency paths from ``otool -L`` (header lines end with ``:``)."""

    libraries: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        path = re.sub(r"\s+\((?:compatibility|current) version.*\)$", "", stripped)
        if path not in libraries:
            libraries.append(path)
    return libraries


def parse_otool_install_id(text: str) -> str:
    """The install name from ``otool -D`` (empty for executables / bundles)."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    ids = [line for line in lines if not line.endswith(":")]
    return ids[0] if ids else ""


def parse_otool_rpaths(text: str) -> list[str]:
    """``LC_RPATH`` entries from ``otool -l``."""

    rpaths: list[str] = []
    in_rpath = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("cmd "):
            in_rpath = stripped == "cmd LC_RPATH"
            continue
        if in_rpath and stripped.startswith("path "):
            path = re.sub(r"\s+\(offset \d+\)$", "", stripped[len("path "):])
            if path not in rpaths:
                rpaths.append(path)
            in_rpath = False
    return rpaths


def is_system_reference(reference: str) -> bool:
    return reference.startswith(SYSTEM_PREFIXES)


def is_external_reference(reference: str) -> bool:
    """An absolute path that is neither the OS nor relative to the bundle."""

    return reference.startswith("/") and not is_system_reference(reference)


# ── planning ─────────────────────────────────────────────────────────────────

@dataclass
class BinaryInfo:
    path: Path
    libraries: list[str]
    install_id: str = ""
    rpaths: list[str] = field(default_factory=list)


@dataclass
class FixPlan:
    changes: list[tuple[str, str]] = field(default_factory=list)
    new_id: str = ""
    delete_rpaths: list[str] = field(default_factory=list)
    add_rpaths: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def install_name_tool_args(self) -> list[str]:
        args: list[str] = []
        if self.new_id:
            args += ["-id", self.new_id]
        for old, new in self.changes:
            args += ["-change", old, new]
        for rpath in self.delete_rpaths:
            args += ["-delete_rpath", rpath]
        for rpath in self.add_rpaths:
            args += ["-add_rpath", rpath]
        return args


def index_bundle_files(files: list[Path]) -> dict[str, list[Path]]:
    """Files by basename, so a reference can be matched to its bundled copy."""

    index: dict[str, list[Path]] = {}
    for path in files:
        index.setdefault(path.name, []).append(path)
    return index


def _common_suffix_length(left: PurePosixPath, right: PurePosixPath) -> int:
    count = 0
    for a, b in zip(reversed(left.parts), reversed(right.parts)):
        if a != b:
            break
        count += 1
    return count


def find_bundled_copy(reference: str, index: dict[str, list[Path]]) -> Path | None:
    """The bundled file that best matches an absolute or ``@rpath`` reference."""

    wanted = PurePosixPath(reference)
    candidates = index.get(wanted.name, [])
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda path: (_common_suffix_length(PurePosixPath(path.as_posix()), wanted), -len(path.parts)),
    )


def executable_relative(target: Path, executable_dir: Path) -> str:
    relative = os.path.relpath(target, executable_dir).replace(os.sep, "/")
    return f"@executable_path/{relative}"


def expand_reference(reference: str, binary: Path, executable_dir: Path) -> Path | None:
    """Resolve ``@executable_path`` / ``@loader_path`` to a bundle path."""

    for token, base in (("@executable_path", executable_dir), ("@loader_path", binary.parent)):
        if reference == token:
            return base
        if reference.startswith(token + "/"):
            return Path(os.path.normpath(base / reference[len(token) + 1:]))
    return None


def _rpath_resolves(suffix: str, rpaths: list[str], binary: Path, executable_dir: Path) -> bool:
    for rpath in rpaths:
        base = expand_reference(rpath, binary, executable_dir)
        if base is not None and (base / suffix).exists():
            return True
    return False


def plan_fixes(info: BinaryInfo, executable_dir: Path, index: dict[str, list[Path]]) -> FixPlan:
    """Rewrite build-machine references to the copies shipped in the bundle."""

    plan = FixPlan()
    if info.install_id and is_external_reference(info.install_id):
        plan.new_id = executable_relative(info.path, executable_dir)
    for library in info.libraries:
        if library == info.install_id or not is_external_reference(library):
            continue
        target = find_bundled_copy(library, index)
        if target is None:
            plan.missing.append(library)
        else:
            plan.changes.append((library, executable_relative(target, executable_dir)))

    plan.delete_rpaths = [rpath for rpath in info.rpaths if is_external_reference(rpath)]
    kept_rpaths = [rpath for rpath in info.rpaths if rpath not in plan.delete_rpaths]
    for library in info.libraries:
        if not library.startswith("@rpath/") or library == info.install_id:
            continue
        suffix = library[len("@rpath/"):]
        if _rpath_resolves(suffix, kept_rpaths + plan.add_rpaths, info.path, executable_dir):
            continue
        target = find_bundled_copy(library, index)
        depth = len(PurePosixPath(suffix).parts)
        if target is None or _common_suffix_length(PurePosixPath(target.as_posix()), PurePosixPath(suffix)) < depth:
            continue  # resolved through the loading image's rpaths, if at all
        base = target.parents[depth - 1]
        relative = os.path.relpath(base, info.path.parent).replace(os.sep, "/")
        rpath = "@loader_path" if relative == "." else f"@loader_path/{relative}"
        if rpath not in kept_rpaths and rpath not in plan.add_rpaths:
            plan.add_rpaths.append(rpath)
    return plan


def audit_binary(info: BinaryInfo, executable_dir: Path) -> list[str]:
    """Problems that would stop the bundle from loading on another Mac."""

    issues: list[str] = []
    for reference in [info.install_id, *info.libraries, *info.rpaths]:
        if reference and is_external_reference(reference):
            issues.append(f"{info.path}: references a build-machine path {reference}")
    for library in info.libraries:
        if library == info.install_id:
            continue
        resolved = expand_reference(library, info.path, executable_dir)
        if resolved is not None and not resolved.exists():
            issues.append(f"{info.path}: {library} does not exist in the bundle")
    return issues


# ── launch verification ──────────────────────────────────────────────────────

_DYLD_LINE = re.compile(r"^dyld(?:\[\d+\])?:\s+(?:loaded:\s+)?(?:<[^>]*>\s+)?(?P<path>/.+?)\s*$")


def parse_dyld_loaded_paths(stderr: str) -> list[str]:
    """Image paths printed by ``DYLD_PRINT_LIBRARIES=1``."""

    paths: list[str] = []
    for line in stderr.splitlines():
        match = _DYLD_LINE.match(line.strip())
        if match:
            paths.append(match.group("path"))
    return paths


def foreign_loaded_paths(
    paths: list[str], forbidden_roots: list[Path], *, bundle: Path | None = None,
) -> list[str]:
    """Loaded images that come from the build environment instead of the bundle."""

    roots = [root.resolve() for root in forbidden_roots]
    bundle_root = bundle.resolve() if bundle is not None else None
    foreign: list[str] = []
    for path in paths:
        resolved = Path(path).resolve()
        # The built .app lives inside the checkout, which is itself forbidden.
        # Resolve symlinks so a bundled link cannot hide an environment library.
        if bundle_root is not None and resolved.is_relative_to(bundle_root):
            continue
        if any(resolved.is_relative_to(root) for root in roots):
            foreign.append(path)
    return foreign


# ── Apple tool wrappers (macOS only) ─────────────────────────────────────────

def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, text=True, capture_output=True, **kwargs)
    if result.returncode != 0:
        if command[0] != OTOOL:  # otool failing on a non-image is expected
            print(f"{' '.join(command)} failed ({result.returncode}):\n{result.stdout}{result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result


def is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACHO_MAGICS
    except OSError:
        return False


def bundle_files(app: Path) -> list[Path]:
    return sorted(path for path in (app / "Contents").rglob("*") if path.is_file() and not path.is_symlink())


def read_binary_info(path: Path) -> BinaryInfo | None:
    try:
        libraries = parse_otool_libraries(_run([OTOOL, "-L", str(path)]).stdout)
        install_id = parse_otool_install_id(_run([OTOOL, "-D", str(path)]).stdout)
        rpaths = parse_otool_rpaths(_run([OTOOL, "-l", str(path)]).stdout)
    except subprocess.CalledProcessError:
        return None  # a Mach-O magic on something otool cannot read
    return BinaryInfo(path, libraries, install_id if install_id != str(path) else "", rpaths)


def fix_bundle_references(app: Path) -> tuple[list[str], list[Path]]:
    """Rewrite external references in every Mach-O file.

    Returns the remaining issues and the files that were rewritten (their
    signatures are now invalid).
    """

    executable_dir = app / "Contents" / "MacOS"
    files = bundle_files(app)
    index = index_bundle_files(files)
    binaries = [path for path in files if is_macho(path)]
    issues: list[str] = []
    rewritten: list[Path] = []
    for path in binaries:
        info = read_binary_info(path)
        if info is None:
            continue
        plan = plan_fixes(info, executable_dir, index)
        issues += [f"{path}: {library} is not shipped in the bundle" for library in plan.missing]
        args = plan.install_name_tool_args()
        if args:
            _run([INSTALL_NAME_TOOL, *args, str(path)])
            rewritten.append(path)
            for old, new in plan.changes:
                print(f"  {path.relative_to(app)}: {old} -> {new}")
    print(f"Checked {len(binaries)} Mach-O files, rewrote {len(rewritten)}.")
    return issues, rewritten


def audit_bundle(app: Path) -> list[str]:
    executable_dir = app / "Contents" / "MacOS"
    issues: list[str] = []
    for path in bundle_files(app):
        if not is_macho(path):
            continue
        info = read_binary_info(path)
        if info is not None:
            issues += audit_binary(info, executable_dir)
    return issues


def _codesign(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([CODESIGN, *args], text=True, capture_output=True)


def _report(result: subprocess.CompletedProcess, what: str) -> None:
    output = (result.stdout + result.stderr).strip()
    print(f"{what}: exit {result.returncode}" + (f"\n{output}" if output else ""))


def codesign_bundle(app: Path, rewritten: list[Path]) -> None:
    """Ad-hoc sign again what ``install_name_tool`` invalidated.

    Nuitka has already signed every file.  Only the rewritten nested files are
    signed again, one by one, and then the bundle itself (which covers the main
    executable).  ``--deep`` is a last resort: it treats the framework folders
    PySide6 ships inside ``Contents/MacOS`` as nested bundles and can refuse them.
    """

    # Extended attributes ("resource fork, Finder information, or similar
    # detritus") make codesign refuse the bundle.
    subprocess.run([XATTR, "-cr", str(app)], check=False)
    main_executable = app / "Contents" / "MacOS" / app.stem
    sign = ("--force", "--sign", "-", "--timestamp=none")
    for path in rewritten:
        if path == main_executable:
            continue
        result = _codesign(*sign, str(path))
        if result.returncode != 0:
            _report(result, f"codesign {path.relative_to(app)}")
            raise SystemExit(f"Could not re-sign {path}")
    result = _codesign(*sign, str(app))
    if result.returncode != 0:
        _report(result, "codesign (bundle)")
        result = _codesign(*sign, "--deep", str(app))
        if result.returncode != 0:
            _report(result, "codesign --deep (bundle)")
            raise SystemExit(f"Could not re-sign {app}")
    result = _codesign("--verify", "--verbose=2", str(app))
    _report(result, "codesign --verify")
    if result.returncode != 0:
        raise SystemExit(f"The signature of {app} does not verify")
    # Informational: nested code is checked for real by launching the app.
    _report(_codesign("--verify", "--deep", "--strict", "--verbose=2", str(app)), "codesign --verify --deep --strict")


def make_self_contained(app: Path) -> None:
    issues, rewritten = fix_bundle_references(app)
    codesign_bundle(app, rewritten)
    issues += audit_bundle(app)
    if issues:
        raise SystemExit("The app bundle is not self-contained:\n" + "\n".join(sorted(set(issues))))
    print(f"{app.name}: every library reference stays inside the bundle; signature verified.")
