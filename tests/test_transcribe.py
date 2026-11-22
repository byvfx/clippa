"""The transcribe stage (clippa/transcribe.py): the pure fix-ups on WhisperX's words. The
GPU, Modal and ffmpeg paths are not run here."""
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
