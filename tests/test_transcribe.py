"""The transcribe stage (clippa/transcribe.py): the pure fix-ups on WhisperX's words and the
Modal upload's format. The GPU, Modal and ffmpeg themselves are not run here."""
import sys

import pytest

from clippa import transcribe


def test_fill_missing_times_spreads_unaligned_words_between_their_neighbours():
    words = [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "7"}, {"word": "%"},
             {"word": "b", "start": 4.0, "end": 5.0}]
    assert transcribe.fill_missing_times(words) == 2
    assert [(w["start"], w["end"]) for w in words[1:3]] == [(1.0, 2.5), (2.5, 4.0)]
    assert words[1]["interpolated"] is True and words[1]["score"] is None
    assert "interpolated" not in words[0]


def test_fill_missing_times_at_the_ends():
    tail = [{"word": "a", "start": 0.0, "end": 1.0}, {"word": "9"}]   # nothing after: a zero-length word
    assert transcribe.fill_missing_times(tail) == 1 and (tail[1]["start"], tail[1]["end"]) == (1.0, 1.0)
    head = [{"word": "#"}, {"word": "a", "start": 2.0, "end": 3.0}]   # nothing before: it sits where a starts
    assert transcribe.fill_missing_times(head) == 1 and (head[0]["start"], head[0]["end"]) == (2.0, 2.0)
    assert transcribe.fill_missing_times([]) == 0


def test_a_modal_lookup_finds_the_old_app_until_the_new_one_is_deployed(monkeypatch):
    # #286 pass 3: clippa-whisperx is the default; until `clippa transcribe --deploy` makes
    # it, a lookup finds the old clipper-whisperx. Another missing app still fails.
    import types

    class NotFound(Exception):
        pass

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(exception=types.SimpleNamespace(NotFoundError=NotFound)))
    deployed = {"clipper-whisperx"}

    class Ref:
        def __init__(self, app, name):
            self.app, self.name = app, name

        def hydrate(self):
            if self.app not in deployed:
                raise NotFound(self.app)
            return self

    found, used = transcribe.modal_found(Ref, "clippa-whisperx", "WhisperX")
    assert (found.app, used) == ("clipper-whisperx", "clipper-whisperx")
    deployed.add("clippa-whisperx")
    assert transcribe.modal_found(Ref, "clippa-whisperx", "WhisperX")[1] == "clippa-whisperx"
    with pytest.raises(NotFound):
        transcribe.modal_found(Ref, "my-app", "WhisperX")


def test_a_flac_over_the_limit_goes_up_as_opus(tmp_path, defaults, monkeypatch):
    """#422: an 18 h stream's 1.3 GB FLAC took the NAS out of memory in Modal's multipart
    upload (1 GiB and up). Over [transcribe] modal_opus_over_mb the upload is opus instead."""
    tc = {**defaults["transcribe"], "modal_gpu": "L4"}
    assert tc["modal_audio"] == "flac" and tc["modal_opus_over_mb"] == 900
    assert not transcribe.too_big_for_flac(tc, "flac", 900_000_000)
    assert transcribe.too_big_for_flac(tc, "flac", 900_000_001)
    assert not transcribe.too_big_for_flac(tc, "opus", 2_000_000_000)    # already opus
    assert not transcribe.too_big_for_flac({**tc, "modal_opus_over_mb": 0}, "flac", 2_000_000_000)
    assert not transcribe.too_big_for_flac({**tc, "modal_opus_over_mb": -1}, "flac", 2_000_000_000)

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"\0" * 1044)
    encoded, sent = [], []

    def encode(w, fmt):  # 200 bytes of FLAC, 50 of opus
        encoded.append(fmt)
        return b"x" * {"flac": 200, "opus": 50}[fmt], transcribe.MODAL_CODECS[fmt][0]

    class Worker:
        class transcribe:  # stands in for the Modal method
            @staticmethod
            def remote(data, suffix, settings):
                sent.append((suffix, len(data)))
                return {"gpu": "L4", "timings": {}, "dur": 1.0}

    monkeypatch.setattr(transcribe, "modal_encode", encode)
    monkeypatch.setattr(transcribe, "modal_worker", lambda tc, gpu: Worker)
    transcribe.transcribe_modal(wav, {**tc, "modal_opus_over_mb": 100e-6})   # a 100-byte limit
    assert encoded == ["flac", "opus"] and sent == [(".ogg", 50)]
    encoded.clear(), sent.clear()
    transcribe.transcribe_modal(wav, {**tc, "modal_opus_over_mb": 200e-6})   # at the limit: lossless
    assert encoded == ["flac"] and sent == [(".flac", 200)]


def test_a_failed_modal_encode_leaves_no_audio_behind(tmp_path, monkeypatch):
    """A killed or failed ffmpeg mustn't leave a GB of audio.modal.flac in the stream folder."""
    import subprocess

    wav = tmp_path / "audio.wav"

    def fail(cmd, check):
        (tmp_path / "audio.modal.flac").write_bytes(b"part")   # ffmpeg got partway
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(transcribe.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        transcribe.modal_encode(wav, "flac")
    assert not (tmp_path / "audio.modal.flac").exists()
