"""Covers the voice side's first-launch and audio plumbing: device lookup by name
(audio_devices), the PTT chirps (voice/earcons), and the speech-model download with
progress reported to the launcher (voice_input + startup_status). No real audio device,
network or model — sounddevice and the download are faked."""
from types import SimpleNamespace

import numpy as np
import pytest

import audio_devices
import startup_status
from voice import earcons, voice_input

# What Windows reports: the same headset once per host API (0 = MME, the default,
# which truncates names; 1 = WASAPI).
DEVICES = [
    {"name": "Headset (Arctis 7)", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
    {"name": "Headset (Arctis 7)", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Headset (Arctis 7)", "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0},
    {"name": "Speakers (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    {"name": "Only on WASAPI", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2},
]


@pytest.fixture
def fake_devices(monkeypatch):
    monkeypatch.setattr(audio_devices.sd, "query_devices", lambda: DEVICES)
    monkeypatch.setattr(audio_devices.sd, "default", SimpleNamespace(hostapi=0))


def test_pickers_list_each_device_once_from_the_default_host_api(fake_devices):
    assert audio_devices.device_names("input") == ["Headset (Arctis 7)"]
    assert audio_devices.device_names("output") == ["Headset (Arctis 7)", "Speakers (Realtek(R) Audio)"]


def test_duplicate_names_resolve_to_the_default_host_api_device(fake_devices):
    # sounddevice's own name lookup raises "Multiple devices found" here.
    assert audio_devices.resolve("Headset (Arctis 7)", "input") == 0
    assert audio_devices.resolve("Headset (Arctis 7)", "output") == 1


def test_device_only_on_another_host_api_still_resolves(fake_devices):
    assert audio_devices.resolve("Only on WASAPI", "output") == 4


def test_missing_or_unset_device_means_system_default(fake_devices):
    assert audio_devices.resolve("Unplugged Headset", "input") is None
    assert audio_devices.resolve(None, "output") is None


def test_chirps_are_short_quiet_and_mirror_each_other():
    for chirp in (earcons.START_CHIRP, earcons.STOP_CHIRP):
        assert chirp.dtype == np.float32
        assert len(chirp) / earcons.SAMPLE_RATE < 0.2
        assert np.abs(chirp).max() <= earcons.VOLUME + 1e-6
    np.testing.assert_array_equal(
        earcons.START_CHIRP[: len(earcons.START_CHIRP) // 2], earcons.STOP_CHIRP[len(earcons.STOP_CHIRP) // 2:],
    )


def test_chirps_can_be_turned_off(monkeypatch):
    played = []
    monkeypatch.setattr(earcons.sd, "play", lambda *args, **kwargs: played.append(args))
    monkeypatch.setenv("ALICE_PTT_SOUNDS", "0")
    earcons.play(earcons.START_CHIRP)
    assert played == []


def test_a_chirp_failure_never_raises(monkeypatch):
    def _broken(*args, **kwargs):
        raise RuntimeError("no output device")

    monkeypatch.setattr(earcons.sd, "play", _broken)
    monkeypatch.setenv("ALICE_PTT_SOUNDS", "1")
    earcons.play(earcons.STOP_CHIRP)


@pytest.fixture
def stages(monkeypatch):
    seen = []
    monkeypatch.setattr(startup_status, "_listener", lambda stage, done, total: seen.append((stage, done, total)))
    return seen


def test_cached_model_skips_the_download(monkeypatch, stages):
    def _fake_download(size, local_files_only=False):
        assert local_files_only
        return "/cache/small"

    monkeypatch.setattr(voice_input, "download_model", _fake_download)
    assert voice_input._ensure_downloaded("small") == "/cache/small"
    assert stages == []


def test_first_launch_download_reports_progress(monkeypatch, stages, tmp_path):
    calls = []

    def _fake_download(size, local_files_only=False):
        calls.append(local_files_only)
        if local_files_only:
            raise FileNotFoundError("not cached")
        return "/cache/small"

    monkeypatch.setattr(voice_input, "download_model", _fake_download)
    monkeypatch.setattr(voice_input, "_download_size", lambda repo_id: 486_000_000)
    monkeypatch.setattr(voice_input.hf_constants, "HF_HUB_CACHE", str(tmp_path))

    assert voice_input._ensure_downloaded("small") == "/cache/small"
    assert calls == [True, False]
    assert stages[0] == (startup_status.DOWNLOADING_VOICE, 0, 486_000_000)


def test_progress_is_capped_at_the_expected_total(tmp_path):
    (tmp_path / "blob").write_bytes(b"x" * 300)
    assert voice_input._folder_bytes(tmp_path) == 300
    assert min(voice_input._folder_bytes(tmp_path), 200) == 200


def test_a_broken_progress_display_never_stops_startup(monkeypatch):
    def _broken(stage, done, total):
        raise RuntimeError("widget gone")

    monkeypatch.setattr(startup_status, "_listener", _broken)
    startup_status.report(startup_status.READY)
