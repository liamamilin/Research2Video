"""Install the pinned biliup executable inside ignored state/, with release SHA-256 verification."""

from __future__ import annotations

import hashlib
import argparse
import os
import platform
import subprocess
import tempfile
import urllib.request
from pathlib import Path

VERSION = "v1.3.4"
ASSETS = {
    ("Darwin", "arm64"): ("aarch64-macos", "1bfdb13a092600907e6111c0ed08c30af4abe5c8ed0b041e6d98b94efe376d46"),
    ("Darwin", "x86_64"): ("x86_64-macos", "a829852f5f120e408a895bba0bce0508566aab414718ce5e3089abb87835f6d3"),
    ("Linux", "x86_64"): ("x86_64-linux", "c842430e242234766b0a7de92c761f2ead6aad4667b35873332a4d8745d6c952"),
    ("Linux", "aarch64"): ("aarch64-linux", "f2c6813f44e6cb758af14df46a54e21210e087caf148bf677cba3091c2597f18"),
}


def install(local_archive: Path | None = None) -> Path:
    target = ASSETS.get((platform.system(), platform.machine()))
    if target is None:
        raise SystemExit("This OS/architecture needs a manual biliup installation (set AREC_BILIUP_BIN).")
    suffix, expected = target
    name = f"biliupR-{VERSION}-{suffix}.tar.xz"
    url = f"https://github.com/biliup/biliup/releases/download/{VERSION}/{name}"
    destination = Path(__file__).resolve().parents[1] / "state/platforms/runtime/bin/biliup"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        archive = Path(folder) / name
        digest = hashlib.sha256()
        request = urllib.request.Request(url, headers={"User-Agent": "AI-Research-platform-setup"})
        source = local_archive.open("rb") if local_archive else urllib.request.urlopen(request, timeout=60)
        with source as response, archive.open("wb") as stream:
            while block := response.read(1024 * 1024):
                digest.update(block)
                stream.write(block)
        if digest.hexdigest() != expected:
            raise SystemExit("Release checksum mismatch; nothing installed.")
        # System tar handles xz even when this Python was built without lzma.
        listing = subprocess.run(["tar", "-tf", str(archive)], capture_output=True, check=True).stdout.decode()
        member = next((name for name in listing.splitlines() if Path(name).name == "biliup"), None)
        if member is None:
            raise SystemExit("No biliup executable in this release.")
        executable = subprocess.run(["tar", "-xOf", str(archive), member], capture_output=True, check=True).stdout
        fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".biliup-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(executable)
            os.chmod(temporary, 0o755)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an already-downloaded official release archive (checksum still required)")
    print(install(parser.parse_args().archive))
