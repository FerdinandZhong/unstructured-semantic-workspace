"""Expose the archived transcript only for the exact synthetic Jade recording."""

from hashlib import sha256
import json
from math import isfinite
from pathlib import Path


SOURCE_ID = "financial-services:source:jade_call"
AUDIO_PATH = "demo_data/hero/out/jade_logistics_rm_call.mp3"
AUDIO_LIMIT = 1024 * 1024
FIXTURE_LIMIT = 16 * 1024


def archived_transcript(fixture_path, v1_root, asset):
    """Fail closed when the local original or synthetic archive pin differs."""
    if (asset.get("id"), asset.get("root"), asset.get("kind"), asset.get("scope"), asset.get("path")) != (
        "jade_call", "v1", "synthetic_audio", "jade_demo", AUDIO_PATH,
    ):
        return []
    root = Path(v1_root).resolve()
    path = root / AUDIO_PATH
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return []
        with path.open("rb") as stream:
            audio = stream.read(AUDIO_LIMIT + 1)
        if len(audio) != asset["bytes"] or sha256(audio).hexdigest() != asset["sha256"]:
            return []
        with fixture_path.open("rb") as stream:
            fixture_raw = stream.read(FIXTURE_LIMIT + 1)
        if len(fixture_raw) > FIXTURE_LIMIT:
            return []
        return _observations(json.loads(fixture_raw), asset["sha256"])
    except (OSError, ValueError, TypeError, KeyError):
        return []


def _observations(fixture, expected):
    if (fixture["version"] != 1 or fixture["sourceId"] != SOURCE_ID
            or fixture["audioSha256"] != expected or fixture["synthetic"] is not True):
        raise ValueError("Jade transcript source pin mismatch")
    text = fixture["text"]
    if not isinstance(text, str) or sha256(text.encode()).hexdigest() != fixture["archive"]["textSha256"]:
        raise ValueError("Jade transcript text pin mismatch")
    previous_end, previous_time = 0, 0
    spans = fixture["spans"]
    if not 1 <= len(spans) <= 100:
        raise ValueError("Jade transcript exceeds the bounded segment count")
    observations = []
    for index, span in enumerate(spans, 1):
        first, last = span["start"], span["end"]
        begin, end = span["startSeconds"], span["endSeconds"]
        speaker, language = span["speaker"], span["language"]
        if (type(first) is not int or type(last) is not int or not previous_end <= first < last <= len(text)
                or type(begin) not in (int, float) or type(end) not in (int, float)
                or not (isfinite(begin) and isfinite(end) and previous_time <= begin < end)
                or not isinstance(speaker, str) or not isinstance(language, str)):
            raise ValueError("Invalid Jade transcript span")
        quote = text[first:last]
        if not quote.strip():
            raise ValueError("Empty Jade transcript segment")
        observations.append({
            "id": f"financial-services:observation:jade-call-{index:02}",
            "title": quote.strip(), "description": "Verbatim archived segment of the synthetic recording.",
            "sourceId": SOURCE_ID, "observationRole": "assertion", "quote": quote,
            "citation": {
                "kind": "transcript", "locator": "timestamped transcript", "sha256": expected,
                "startSeconds": begin, "endSeconds": end, "speaker": speaker,
                "language": language, "startOffset": first, "endOffset": last,
            },
        })
        previous_end, previous_time = last, begin
    return observations
