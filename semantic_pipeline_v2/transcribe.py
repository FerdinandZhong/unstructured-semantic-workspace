"""Bounded Mistral audio transcription for a pinned audio or video source."""

from hashlib import sha256
import json
from math import isfinite
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from .ocr import NoRedirects, OcrError


MAX_MEDIA = 25 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
MAX_SEGMENTS = 1500       # mapped evidence accepts 1600 lines per source
URL = "https://api.mistral.ai/v1/audio/transcriptions"
AUDIO = {"audio/mpeg", "audio/mp4", "audio/wav", "audio/ogg", "audio/flac", "audio/x-m4a", "audio/webm"}
VIDEO = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska"}


def configuration():
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    return MistralTranscribe(key, os.environ.get("MISTRAL_AUDIO_MODEL", "voxtral-mini-latest"))


class MistralTranscribe:
    def __init__(self, key, model="voxtral-mini-latest", *, opener=None):
        self._key = key if key and not any(c.isspace() for c in key) else ""
        self.model = model
        self._opener = opener if opener is not None else build_opener(NoRedirects())

    @property
    def available(self):
        return bool(self._key)

    def process(self, filename, mime, payload, *, source_id=None):
        mime = mime.split(";", 1)[0]
        if len(payload) > MAX_MEDIA:
            raise OcrError(413, "Transcription files are limited to 25 MiB")
        if mime not in AUDIO | VIDEO:
            raise OcrError(415, "Transcription accepts audio or video files")
        if not self.available:
            raise OcrError(503, "Mistral transcription is unavailable. Configure MISTRAL_API_KEY on the server.")
        sent_name, sent_mime, sent = filename, mime, payload
        if mime in VIDEO:
            sent_name, sent_mime, sent = "audio.wav", "audio/wav", self._audio_track(payload)
        result = self._post(sent_name, sent_mime, sent)
        text, segments = validated_segments(result)
        source = {"filename": filename, "mimeType": mime, "sha256": sha256(payload).hexdigest()}
        if source_id is not None:
            source["id"] = source_id
        return {"status": "completed", "provider": "mistral", "model": result["model"], "source": source,
                "pages": [{"index": 0, "markdown": text, "segments": segments}]}

    @staticmethod
    def _audio_track(payload):
        # ponytail: video needs ffmpeg on the server; without it video stays refused.
        if not shutil.which("ffmpeg"):
            raise OcrError(415, "Video transcription needs ffmpeg on the server. Use an audio file.")
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder, "in"), Path(folder, "out.wav")
            source.write_bytes(payload)
            try:
                done = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-i",
                                       str(source), "-vn", "-ac", "1", "-ar", "16000", "-y", str(target)],
                                      capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired):
                raise OcrError(422, "The audio track could not be extracted from this video.") from None
            if done.returncode or not target.is_file():
                raise OcrError(422, "The audio track could not be extracted from this video.")
            audio = target.read_bytes()
        if len(audio) > MAX_MEDIA:
            raise OcrError(413, "The extracted audio track exceeds 25 MiB. Use a shorter video.")
        return audio

    def _post(self, name, mime, data):
        boundary = uuid4().hex
        fields = {"model": self.model, "diarize": "true", "timestamp_granularities": "segment"}
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            for key, value in fields.items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{_ascii(name)}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = Request(URL, method="POST", data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": "Bearer " + self._key})
        try:
            with self._opener.open(request, timeout=300) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            raise OcrError(502, {
                401: "Mistral rejected the server credential.", 403: "Mistral denied the transcription request.",
                413: "Mistral rejected the audio size.", 422: "Mistral could not transcribe this audio.",
                429: "Mistral transcription rate limit reached.",
            }.get(error.code, "Mistral transcription request failed.")) from None
        except (URLError, TimeoutError, OSError):
            raise OcrError(502, "Mistral transcription connection failed or timed out.") from None
        if len(raw) > MAX_RESPONSE:
            raise OcrError(502, "Mistral transcription response exceeded the local size limit.")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise OcrError(502, "Mistral returned unreadable transcription output.") from None


def _ascii(name):
    return "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in name)


def validated_segments(result):
    """One line of text per segment plus aligned time, speaker and language."""
    invalid = OcrError(502, "Mistral returned invalid transcription data.")
    if not isinstance(result, dict) or not isinstance(result.get("model"), str) or not result["model"]:
        raise invalid
    raw = result.get("segments")
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_SEGMENTS:
        raise invalid
    lines, segments = [], []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise invalid
        start, end = item.get("start"), item.get("end")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not (isfinite(start) and isfinite(end)) or not 0 <= start <= end):
            raise invalid
        speaker, language = item.get("speaker_id"), item.get("language") or result.get("language")
        if not (speaker is None or isinstance(speaker, str)) or not (language is None or isinstance(language, str)):
            raise invalid
        text = " ".join(item["text"].split())
        if not text:
            continue
        lines.append(text)
        segments.append({"startSeconds": float(start), "endSeconds": float(end),
                         "speaker": speaker, "language": language})
    if not lines:
        raise invalid
    return "\n".join(lines), segments
