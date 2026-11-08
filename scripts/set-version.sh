#!/usr/bin/env bash
# Write the release version where the project publishes it. `deploy` runs this
# before it makes the tag, so the tag, the package and the Home Assistant
# manifest cannot disagree.
#
#   scripts/set-version.sh 0.1.1
set -euo pipefail

version="${1:?usage: scripts/set-version.sh <version>}"

# The package version, in the `[project]` table only. A dependency may also
# carry a version line, so match the first one.
python3 - "$version" <<'PY'
import pathlib, re, sys

version = sys.argv[1]
path = pathlib.Path("pyproject.toml")
text = path.read_text()
new, count = re.subn(r'(?m)^version = "[^"]*"', f'version = "{version}"', text, count=1)
if count != 1:
    raise SystemExit("pyproject.toml holds no version line")
path.write_text(new)
PY

# The Home Assistant manifest, which is what the person sees in the interface.
# Replace the one line and leave the rest of the file byte for byte, so a
# release does not reformat a file that people also edit by hand.
python3 - "$version" <<'PY'
import pathlib, re, sys

version = sys.argv[1]
path = pathlib.Path("custom_components/voice_router/manifest.json")
if not path.exists():
    raise SystemExit(f"{path} does not exist")
text = path.read_text()
new, count = re.subn(r'"version": "[^"]*"', f'"version": "{version}"', text, count=1)
if count != 1:
    raise SystemExit(f"{path} holds no version line")
path.write_text(new)
PY

printf 'version %s written\n' "$version"