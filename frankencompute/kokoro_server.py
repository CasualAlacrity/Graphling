"""ALICE's voice on frankencompute: Kokoro TTS behind a tiny HTTP API.

Runs on the box itself (not in this repo's Python env) under ~/tts/kokoro-env, as the
kokoro-tts systemd service (frankencompute/kokoro-tts.service). Only the Hetzner server
calls it, over Tailscale -- pilots reach it through the server's /tts route
(server/tts_service.py), never directly. Setup and the reasons for each choice are in
docs/frankencompute-bench.md (TTS) and docs/llm-gateway.md (TTS proxy).

    POST /tts     {"text": "...", "voice": "bf_isabella"}  ->  audio/wav (16-bit, 24 kHz)
                  X-Audio-Seconds: length of the clip
    GET  /health  ->  {"ok": true, "voice": "bf_isabella"}

Standard library HTTP server on purpose: one GPU, ~0.1 s per reply, a handful of
pilots -- a lock around synthesis is all the concurrency this needs, and it keeps the
service to one file plus the kokoro package.
"""
import io
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf
from kokoro import KPipeline

HOST = os.getenv("KOKORO_HOST", "0.0.0.0")
PORT = int(os.getenv("KOKORO_PORT", "8880"))
DEFAULT_VOICE = os.getenv("KOKORO_VOICE", "bf_isabella")
SAMPLE_RATE = 24_000
# A spoken ALICE reply is a few hundred characters; this only stops a runaway request
# from holding the GPU lock for minutes.
MAX_CHARS = 4000

log = logging.getLogger("kokoro_server")

# Kokoro's first voice letter picks the language pipeline: a = American, b = British.
_pipelines: dict[str, KPipeline] = {}
_lock = threading.Lock()


def _pipeline(voice: str) -> KPipeline:
    lang_code = voice[0]
    if lang_code not in _pipelines:
        _pipelines[lang_code] = KPipeline(lang_code=lang_code, device="cuda")
    return _pipelines[lang_code]


def synthesize(text: str, voice: str) -> np.ndarray:
    with _lock:
        chunks = []
        for _, _, audio in _pipeline(voice)(text, voice=voice):
            chunks.append(np.asarray(audio))
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def wav_bytes(audio: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self._json(404, {"error": "not found"})
            return
        self._json(200, {"ok": True, "voice": DEFAULT_VOICE})

    def do_POST(self):
        if self.path != "/tts":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            text = str(body["text"]).strip()
            voice = str(body.get("voice") or DEFAULT_VOICE)
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "expected JSON {\"text\": ..., \"voice\": optional}"})
            return
        if not text:
            self._json(400, {"error": "empty text"})
            return
        if len(text) > MAX_CHARS:
            self._json(413, {"error": f"text longer than {MAX_CHARS} characters"})
            return
        if voice[0] not in ("a", "b"):
            self._json(400, {"error": f"unsupported voice {voice!r}"})
            return

        started = time.monotonic()
        try:
            audio = synthesize(text, voice)
        except Exception:
            log.exception("synthesis failed")
            self._json(500, {"error": "synthesis failed"})
            return
        payload = wav_bytes(audio)
        log.info("%d chars -> %.1fs audio in %.0f ms", len(text), len(audio) / SAMPLE_RATE,
                 (time.monotonic() - started) * 1000)

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Audio-Seconds", f"{len(audio) / SAMPLE_RATE:.2f}")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A002 -- BaseHTTPRequestHandler's signature
        log.debug(format, *args)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Load the model and the default voice before taking requests, so the first pilot
    # doesn't pay the ~8 s load.
    synthesize("Ready.", DEFAULT_VOICE)
    log.info("kokoro ready on %s:%d, default voice %s", HOST, PORT, DEFAULT_VOICE)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
