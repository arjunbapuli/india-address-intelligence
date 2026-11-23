"""Generate spoken test addresses with ElevenLabs TTS.

    export ELEVENLABS_API_KEY=...            # never commit the key
    python -m evaluation.audio.generate_audio                 # all 30
    python -m evaluation.audio.generate_audio --only a01 a02  # a few
    python -m evaluation.audio.generate_audio --model eleven_v3

For each item in addresses.json this writes:
  output/<id>_<voice>.mp3         studio quality (44.1 kHz), for listening
  output/<id>_<voice>_8k.wav      phone quality (8 kHz mono, mu-law), what a call-centre STT actually hears
and output/manifest.csv linking every file to its script, voice and expected address.

Existing files are skipped, so a failed run can simply be re-run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"


def synthesize(text: str, voice_id: str, model: str, key: str, retries: int = 4) -> bytes:
    body = json.dumps({
        "text": text,
        "model_id": model,
        # Slightly lower stability = more natural variation, closer to a real caller
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75, "style": 0.2, "use_speaker_boost": True},
    }).encode()
    for attempt in range(retries):
        req = urllib.request.Request(API.format(voice_id=voice_id), data=body, method="POST",
                                     headers={"xi-api-key": key, "Content-Type": "application/json",
                                              "Accept": "audio/mpeg"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:300]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    raise RuntimeError("unreachable")


def to_phone_quality(mp3: Path, wav: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp3), "-ac", "1", "-ar", "8000",
                    "-c:a", "pcm_mulaw", str(wav)], check=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="eleven_multilingual_v2")
    ap.add_argument("--only", nargs="*", help="item ids, e.g. a01 a07")
    ap.add_argument("--out", default=str(HERE / "output"))
    args = ap.parse_args()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        print("Set ELEVENLABS_API_KEY first.", file=sys.stderr)
        return 2
    items = json.loads((HERE / "addresses.json").read_text(encoding="utf-8"))["items"]
    if args.only:
        items = [i for i in items if i["id"] in set(args.only)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, failed = [], []
    for it in items:
        stem = f"{it['id']}_{it['voice_key']}"
        mp3, wav = out / f"{stem}.mp3", out / f"{stem}_8k.wav"
        try:
            if not mp3.exists():
                mp3.write_bytes(synthesize(it["spoken_text"], it["voice_id"], args.model, key))
            if not wav.exists():
                to_phone_quality(mp3, wav)
            print(f"ok   {stem}  ({mp3.stat().st_size // 1024} KB)")
        except Exception as exc:  # keep going; report at the end
            failed.append((it["id"], str(exc)))
            print(f"FAIL {stem}: {exc}", file=sys.stderr)
            continue
        gt = it["ground_truth"]
        rows.append({"id": it["id"], "mp3": mp3.name, "wav_8k": wav.name if wav.exists() else "",
                     "voice": it["voice_name"], "accent": it["accent"], "style": it["style"], "model": args.model,
                     "spoken_text": it["spoken_text"],
                     "locality": "|".join(gt["locality"]) if isinstance(gt["locality"], list) else gt["locality"],
                     "city": gt["city"], "state": gt["state"], "postal_code": gt["postal_code"] or ""})
    if rows:
        with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f"\n{len(rows)} generated, {len(failed)} failed. Files in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
