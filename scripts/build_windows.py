"""Build the Windows onefile .exe with version metadata, then sign it when a certificate is configured.

Signing (optional) reads, in order:
  WINDOWS_SIGN_PFX           path to a .pfx code-signing certificate, or
  WINDOWS_SIGN_PFX_BASE64    the same .pfx, base64 encoded (CI secrets);
  WINDOWS_SIGN_PFX_PASSWORD  its password;
  WINDOWS_SIGN_TIMESTAMP_URL RFC 3161 timestamp server (default: DigiCert).
Without a certificate the build is left unsigned and says so.
"""
from __future__ import annotations

import argparse
import base64
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.version import __version__  # noqa: E402

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


def nuitka_command(output_filename: str) -> list[str]:
    numeric_version = ".".join((__version__.split("-")[0].split(".") + ["0", "0", "0"])[:4])
    return [
        sys.executable, "-m", "nuitka",
        "--assume-yes-for-downloads", "--standalone", "--onefile",
        "--enable-plugin=pyside6", "--include-qt-plugins=multimedia",
        "--windows-console-mode=disable",
        "--include-package-data=cloudscraper",
        "--windows-icon-from-ico=app/icon.ico",
        "--include-data-file=app/icon.ico=app/icon.ico",
        "--include-data-file=app/data/tag_translations/loveiwara_iwara_tags_localized.json=app/data/tag_translations/loveiwara_iwara_tags_localized.json",
        "--include-data-file=app/data/oreno3d_iwara_map.json=app/data/oreno3d_iwara_map.json",
        # Version resources: unsigned binaries without them look more suspicious to antivirus heuristics.
        "--company-name=Moeary",
        "--product-name=IwaraTool",
        f"--file-version={numeric_version}",
        f"--product-version={numeric_version}",
        "--file-description=IwaraTool - Iwara video downloader",
        "--copyright=Copyright (c) 2026 Moeary. MIT License.",
        f"--output-filename={output_filename}",
        "--output-dir=dist", "--remove-output",
        "main.py",
    ]


def find_signtool() -> str | None:
    found = shutil.which("signtool")
    if found:
        return found
    kits = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Windows Kits", "10", "bin")
    candidates = sorted(glob.glob(os.path.join(kits, "*", "x64", "signtool.exe")))
    return candidates[-1] if candidates else None


def sign(target: Path) -> bool:
    """Sign and timestamp ``target``; False when no certificate is configured."""
    pfx = os.environ.get("WINDOWS_SIGN_PFX", "").strip()
    pfx_b64 = os.environ.get("WINDOWS_SIGN_PFX_BASE64", "").strip()
    if not pfx and not pfx_b64:
        print("No code-signing certificate configured: the build is unsigned.")
        return False
    password = os.environ.get("WINDOWS_SIGN_PFX_PASSWORD", "")
    timestamp_url = os.environ.get("WINDOWS_SIGN_TIMESTAMP_URL", "").strip() or DEFAULT_TIMESTAMP_URL
    allow_untrusted = os.environ.get("WINDOWS_SIGN_ALLOW_UNTRUSTED") == "1"  # self-signed test certificates
    with tempfile.TemporaryDirectory(prefix="iwaratool-sign-") as temp_dir:
        if not pfx:
            pfx = os.path.join(temp_dir, "codesign.pfx")
            Path(pfx).write_bytes(base64.b64decode(pfx_b64))
        signtool = find_signtool()
        if signtool:
            command = [signtool, "sign", "/fd", "SHA256", "/f", pfx]
            if password:
                command += ["/p", password]
            command += ["/tr", timestamp_url, "/td", "SHA256", "/d", "IwaraTool", str(target)]
            subprocess.run(command, check=True)
            subprocess.run([signtool, "verify", "/pa", "/v", str(target)], check=not allow_untrusted)
        else:
            sign_with_powershell(target, pfx, password, timestamp_url, allow_untrusted)
    print(f"Signed {target}")
    return True


# Built into Windows, for machines without the SDK's signtool. Secrets travel in
# environment variables, never on the command line.
_POWERSHELL_SIGN = r"""
$ErrorActionPreference = 'Stop'
$flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
$cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($env:IWT_PFX, $env:IWT_PFX_PASSWORD, $flags)
$result = Set-AuthenticodeSignature -FilePath $env:IWT_TARGET -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $env:IWT_TIMESTAMP_URL
if (-not $result.SignerCertificate) { throw "Signing failed: $($result.StatusMessage)" }
if ($result.Status -ne 'Valid' -and $env:IWT_ALLOW_UNTRUSTED -ne '1') { throw "Signature not trusted: $($result.StatusMessage)" }
"Signature: $($result.Status) / $($result.SignerCertificate.Subject)"
"""


def sign_with_powershell(target: Path, pfx: str, password: str, timestamp_url: str, allow_untrusted: bool) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise SystemExit("Neither signtool.exe nor PowerShell is available for signing.")
    env = {
        **os.environ,
        "IWT_PFX": pfx,
        "IWT_PFX_PASSWORD": password,
        "IWT_TARGET": str(target),
        "IWT_TIMESTAMP_URL": timestamp_url,
        "IWT_ALLOW_UNTRUSTED": "1" if allow_untrusted else "",
    }
    subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_SIGN], env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-filename", default="IwaraTool.exe")
    parser.add_argument("--sign-only", metavar="EXE", help="sign an existing file instead of building")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This build task requires Windows.")
    if args.sign_only:
        sign(Path(args.sign_only).resolve())
        return
    subprocess.run(nuitka_command(args.output_filename), cwd=ROOT, check=True)
    target = ROOT / "dist" / args.output_filename
    sign(target)
    print(f"Created {target}")


if __name__ == "__main__":
    main()
