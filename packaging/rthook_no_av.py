"""PyInstaller runtime hook: stand in for PyAV, which ALICE.exe doesn't ship.

faster-whisper imports `av` at the top of faster_whisper/audio.py, only so its
decode_audio() can read audio *files*. ALICE never calls it: voice_input.py hands
WhisperModel.transcribe() the microphone's numpy array directly. Shipping PyAV would mean
shipping its FFmpeg build, which bundles GPL codecs (x264, x265), so alice.spec excludes
`av` and this hook, run before ALICE's own code, puts a placeholder module in its place.

The placeholder lets `import av` succeed, and anything that actually reaches into it gets
an error naming the cause instead of a bare AttributeError deep inside faster-whisper.
It stays an AttributeError, though: inspect, pickle and the like probe every module in
sys.modules with getattr(module, "__file__", None), which only tolerates AttributeError.
"""
import sys
import types


def _not_shipped(name):
    raise AttributeError(
        f"av.{name}: PyAV isn't included in ALICE.exe (packaging/rthook_no_av.py). "
        "Pass faster-whisper a numpy array, not an audio file."
    )


_stub = types.ModuleType("av")
_stub.__doc__ = "Placeholder for PyAV, which ALICE.exe doesn't ship."
_stub.__getattr__ = _not_shipped
sys.modules.setdefault("av", _stub)
