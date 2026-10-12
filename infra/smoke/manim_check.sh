#!/usr/bin/env bash
# Render the two-section fixture inside brainbench/manim with the sandbox flags from design D7
# and assert the exact, stem-derived output paths and one section clip per section.
set -euo pipefail

IMAGE="${1:-brainbench/manim}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
OUT="$(mktemp -d)"
trap 'rm -rf "$WORK" "$OUT"' EXIT
cp "$ROOT/infra/smoke/manim_lesson.py" "$WORK/scene.py"
chmod 755 "$WORK"
chmod 777 "$OUT"

docker run --rm --network none --cpus 2 --memory 4g --pids-limit 256 \
  --read-only --tmpfs /tmp \
  -v "$WORK:/work:ro" -v "$OUT:/out" \
  "$IMAGE" manim -qm --save_sections --media_dir /out -o lesson.mp4 /work/scene.py Lesson

VIDEO="$OUT/videos/scene/720p30/lesson.mp4"
SECTIONS="$OUT/videos/scene/720p30/sections/lesson.json"
test -s "$VIDEO" || { echo "missing $VIDEO" >&2; find "$OUT" -type f >&2; exit 1; }
test -s "$SECTIONS" || { echo "missing $SECTIONS" >&2; find "$OUT" -type f >&2; exit 1; }

python3 - "$SECTIONS" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
sections = json.loads(path.read_text())
names = [s["name"] for s in sections]
assert names == ["intro", "transform"], names
for section in sections:
    clip = path.parent / section["video"]
    assert clip.is_file() and clip.stat().st_size > 0, clip
print(f"sections ok: {names}")
PY

docker run --rm --network none -v "$OUT:/out:ro" "$IMAGE" \
  ffprobe -v error -show_entries format=duration -of csv=p=0 /out/videos/scene/720p30/lesson.mp4
echo "manim sandbox check passed: /out/videos/scene/720p30/lesson.mp4 and sections/lesson.json"
