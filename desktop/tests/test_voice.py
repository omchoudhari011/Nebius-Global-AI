"""Faster, more natural voice: speaking sentence by sentence, barge-in, transcribing at the first
pause, the Hindi voice, and voice ID (nothing here uses a microphone, speaker or real model)."""

import queue
import threading
import time
from collections import deque

import numpy as np
import pytest

from custodian_desktop import voiceid
from custodian_desktop.voice import FRAME, RATE, Listener, Speaker, sentences, to_wav
from custodian_desktop.voiceid import VoiceId, VoiceIdUnavailable, fbank

LOUD = (np.sin(np.linspace(0, 200, FRAME)) * 8000).astype(np.int16)
QUIET = np.zeros(FRAME, dtype=np.int16)


def test_replies_are_split_into_sentences_worth_saying_alone():
    assert sentences("Done. I've set the volume to 30 percent. Anything else?") == [
        "Done. I've set the volume to 30 percent.", "Anything else?"]
    assert sentences("ठीक है। मैंने आवाज़ 30 प्रतिशत कर दी है। और कुछ?") == ["ठीक है। मैंने आवाज़ 30 प्रतिशत कर दी है।", "और कुछ?"]


class _Recording(Speaker):
    def __init__(self):
        super().__init__(voices_dir=None, voice="en", hindi_voice="hi")
        self.events, self.windows = [], []

    def _synthesize(self, voice, text):
        self.events.append(("make", voice, text))
        time.sleep(0.05)
        return np.zeros(10, dtype=np.int16), 22050

    def _play(self, audio, rate):
        self.events.append(("play-start",))
        time.sleep(0.15)
        self.events.append(("play-end",))

    def _say_windows(self, parts, language):
        self.windows.append((language, parts))


def test_the_next_sentence_is_made_while_the_first_one_plays():
    speaker = _Recording()
    speaker._voices = {"en": "lessac", "hi": None}
    speaker.say("The meeting is at three o'clock today. It's in the big room on the second floor. Bring the slides.")
    kinds = [e[0] for e in speaker.events]
    makes = [i for i, k in enumerate(kinds) if k == "make"]
    assert kinds[0] == "make" and kinds.index("play-start") < makes[-1]  # it starts talking before all are made
    assert makes[1] < kinds.index("play-end")  # the second sentence is made while the first plays
    assert kinds.count("play-start") == 3


def test_hindi_and_marathi_get_the_hindi_voice_or_windows_hindi():
    speaker = _Recording()
    speaker._voices = {"en": "lessac", "hi": "pratham"}
    speaker.say("ठीक है, मैंने आवाज़ कम कर दी है।")
    assert {e[1] for e in speaker.events if e[0] == "make"} == {"pratham"}
    speaker = _Recording()
    speaker._voices = {"en": "lessac", "hi": None}  # no Hindi Piper voice installed
    speaker.say("हो, मी आवाज कमी केला.")
    assert speaker.windows[0][0] == "hi" and not speaker.events


def test_stop_cuts_a_reply_off_between_sentences():
    speaker = _Recording()
    speaker._voices = {"en": "lessac", "hi": None}
    thread = threading.Thread(target=speaker.say, args=("One sentence here, long enough. " * 6,))
    thread.start()
    time.sleep(0.1)
    speaker.stop()
    thread.join(timeout=3)
    assert [e[0] for e in speaker.events].count("play-start") < 6


def _listener(**kwargs):
    heard = []
    listener = Listener(lambda wav, early=None: heard.append((wav, early)), lambda _s: None, wake_word=False, **kwargs)
    return listener, heard


def test_saying_the_wake_word_while_it_speaks_cuts_it_off_and_listens():
    barged = []
    listener, heard = _listener(on_barge=lambda: barged.append(True))
    listener.heard_wake_word = lambda frame, speaking: frame is LOUD and speaking
    listener.speaking(True)
    frames: queue.Queue = queue.Queue()
    for frame in [LOUD] * 15 + [QUIET] * 20:
        frames.put(frame)
    assert listener.step(LOUD, frames, deque()) is True
    assert barged == [True] and len(heard) == 1
    assert listener.step(QUIET, queue.Queue(), deque()) is False  # its own voice alone doesn't start anything


def test_with_barge_in_off_nothing_is_heard_while_it_speaks():
    listener, heard = _listener(barge_in=False)
    listener.heard_wake_word = lambda frame, speaking: True
    listener.speaking(True)
    listener.trigger()
    assert listener.step(LOUD, queue.Queue(), deque()) is False and heard == []


def test_transcribing_starts_at_the_first_pause_and_is_used_if_nothing_follows():
    started = []
    listener, _ = _listener(on_early=lambda wav: started.append(wav) or f"early {len(started)}")
    frames: queue.Queue = queue.Queue()
    for frame in [LOUD] * 15 + [QUIET] * 20:
        frames.put(frame)
    audio = listener._record(frames, floor=100.0)
    assert len(started) == 1 and listener.early == "early 1"
    assert len(audio) < FRAME * 30 and to_wav(audio) == started[0]  # exactly the words transcribed early


def test_going_on_after_a_pause_throws_the_early_transcription_away():
    started = []
    listener, _ = _listener(on_early=lambda wav: started.append(wav) or f"early {len(started)}")
    frames: queue.Queue = queue.Queue()
    for frame in [LOUD] * 10 + [QUIET] * 7 + [LOUD] * 10 + [QUIET] * 20:  # a 0.56 s pause, then more words
        frames.put(frame)
    audio = listener._record(frames, floor=100.0)
    assert len(started) == 2 and listener.early == "early 2"  # the second covers everything said
    assert to_wav(audio) == started[1]


def test_a_quiet_laptop_microphone_is_still_heard():
    """A laptop mic array here gives a room noise of 9 and speech around 150: under the old fixed 350,
    every request was "didn't catch that"."""
    listener, _ = _listener()
    speech, room = np.full(FRAME, 150, dtype=np.int16), np.full(FRAME, 9, dtype=np.int16)
    frames: queue.Queue = queue.Queue()
    for frame in [room] * 3 + [speech] * 15 + [room] * 20:
        frames.put(frame)
    audio = listener._record(frames, floor=9.0)
    assert audio is not None and len(audio) >= FRAME * 15


# Voice ID ---------------------------------------------------------------------------------------

def test_filterbank_features_are_the_shape_the_speaker_model_takes():
    one_second = (np.random.default_rng(1).normal(0, 3000, RATE)).astype(np.int16)
    feats = fbank(one_second)
    assert feats.shape == (98, 80) and np.isfinite(feats).all()
    assert np.allclose(feats.mean(axis=0), 0, atol=1e-4)  # the mean is taken off, as WeSpeaker does
    with pytest.raises(ValueError):
        fbank(np.zeros(100, dtype=np.int16))


def _speaker(seed):
    """Stand-in voices: each speaker is a direction; recordings carry it plus a little noise."""
    return np.random.default_rng(seed).normal(scale=1000, size=64)


def _embedder():
    def embed(samples):
        vector = samples[:64].astype(np.float64)
        return vector / np.linalg.norm(vector)
    return embed


def _recording(voice, noise_seed, seconds=2.0):
    rng = np.random.default_rng(noise_seed)
    head = voice + rng.normal(scale=300, size=64)
    tail = np.zeros(int(RATE * seconds) - 64)
    return np.concatenate([head, tail])


def test_voice_id_accepts_the_enrolled_voice_and_refuses_another(tmp_path):
    me, someone = _speaker(1), _speaker(2)
    vid = VoiceId(embedder=_embedder, path=tmp_path / "print.npy")
    with pytest.raises(VoiceIdUnavailable, match="no voice is set up"):  # fails closed before enrolling
        vid.is_owner(_recording(me, 9))
    vid.enroll([_recording(me, n) for n in range(3)])
    assert vid.is_owner(_recording(me, 10))[0] is True
    assert vid.is_owner(_recording(someone, 11))[0] is False
    assert vid.is_owner(_recording(me, 12, seconds=0.5)) == (False, 0.0)  # too short to tell
    with pytest.raises(ValueError):
        vid.enroll([_recording(me, 1, seconds=0.3)] * 3)


def test_a_missing_model_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(voiceid, "MODEL_FILE", tmp_path / "missing.onnx")
    vid = VoiceId(path=tmp_path / "print.npy", embedder=lambda: voiceid.Embedder(tmp_path / "missing.onnx"))
    np.save(tmp_path / "print.npy", np.ones(4) / 2)
    with pytest.raises(VoiceIdUnavailable, match="isn't installed"):
        vid.is_owner(np.zeros(RATE * 2, dtype=np.int16))


def test_someone_elses_voice_is_never_transcribed_or_sent(tmp_path):
    from custodian_desktop.__main__ import App

    class Client:
        def __init__(self):
            self.heard, self.chats = [], []

        def transcribe(self, wav):
            self.heard.append(wav)
            return "lock my pc"

        def chat(self, text, conversation_id=None):
            self.chats.append(text)
            return {"conversation_id": 1, "reply": "Locked."}

    class Window:
        def __init__(self):
            self.shown = []

        def show(self, title, **kwargs):
            self.shown.append(title)

    app = App.__new__(App)
    app.client, app.window = Client(), Window()
    app.settings = type("S", (), {"voice_id": True, "speak_replies": False})()
    app.voiceid = VoiceId(embedder=_embedder, path=tmp_path / "print.npy")
    app.voiceid.enroll([_recording(_speaker(1), n) for n in range(3)])
    app.awaiting = app.enrolling = None
    app.conversation_id, app.last_turn = None, 0.0
    stranger = to_wav(_recording(_speaker(2), 5).astype(np.int16))
    app.on_speech(stranger)
    assert app.client.heard == [] and app.client.chats == [] and "Not your voice" in app.window.shown


def test_cutting_a_reply_off_leaves_no_speech_thread_behind():
    speaker = _Recording()
    speaker._voices = {"en": "lessac", "hi": None}
    before = threading.active_count()
    thread = threading.Thread(target=speaker.say, args=("A sentence that is long enough to stand. " * 8,))
    thread.start()
    time.sleep(0.08)
    speaker.stop()
    thread.join(timeout=3)
    time.sleep(0.3)
    assert threading.active_count() <= before


def test_filterbank_features_match_kaldis_which_the_speaker_model_was_trained_on():
    knf = pytest.importorskip("kaldi_native_fbank")
    t = np.arange(RATE * 2) / RATE
    samples = (np.sin(2 * np.pi * 220 * t) * 6000 + np.random.default_rng(0).normal(0, 500, len(t))).astype(np.int16)
    opts = knf.FbankOptions()
    opts.frame_opts.dither, opts.frame_opts.window_type, opts.mel_opts.num_bins, opts.use_energy = 0.0, "hamming", 80, False
    reference = knf.OnlineFbank(opts)
    reference.accept_waveform(RATE, samples.astype(np.float32).tolist())  # WeSpeaker feeds int16-scale samples
    reference.input_finished()
    kaldi = np.stack([reference.get_frame(i) for i in range(reference.num_frames_ready)])
    assert np.abs(fbank(samples) - (kaldi - kaldi.mean(axis=0))).max() < 1e-3


class _Mic:
    """sounddevice's surface: InputStream(callback=...) as a context manager, fed by a thread. The
    first stream sends a few frames then nothing (the PC slept); the next ones keep sending."""

    def __init__(self, fail_first=0):
        self.opened, self.fail_first, self.restarts = 0, fail_first, 0

    def _terminate(self):
        self.restarts += 1

    def _initialize(self):
        pass

    def InputStream(self, *, callback, **_):  # noqa: N802 - sounddevice's name
        mic = self
        mic.opened += 1
        if mic.opened <= mic.fail_first:
            raise RuntimeError("Error opening InputStream: Invalid device")
        frames = 3 if mic.opened == mic.fail_first + 1 else 10_000

        class Stream:
            def __enter__(self):
                self.stop = threading.Event()

                def feed():
                    for _ in range(frames):
                        if self.stop.wait(0.01):
                            return
                        callback(np.zeros((FRAME, 1), dtype=np.int16), FRAME, None, None)

                threading.Thread(target=feed, daemon=True).start()
                return self

            def __exit__(self, *exc):
                self.stop.set()

        return Stream()


def test_the_microphone_is_opened_again_when_it_goes_quiet_or_cant_be_opened(monkeypatch):
    import sys

    from custodian_desktop import voice

    mic = _Mic(fail_first=1)
    monkeypatch.setitem(sys.modules, "sounddevice", mic)
    monkeypatch.setattr(voice, "STALL_SECONDS", 0.2)
    listener, _ = _listener()
    listener._load_model = lambda: None
    waits = []
    real_wait = listener._stop.wait
    monkeypatch.setattr(listener._stop, "wait", lambda seconds: waits.append(seconds) or real_wait(0.01))
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while mic.opened < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    listener.stop()
    thread.join(timeout=3)
    # 1: couldn't be opened; 2: sent 3 frames and went quiet; 3: opened again, and listening.
    assert mic.opened >= 3 and mic.restarts >= 2 and not thread.is_alive()


def test_the_second_opinion_stops_lookalikes_and_hears_what_the_stock_model_misses():
    from custodian_desktop import wake

    assert wake.heard(0.96, 0.01) is False  # "Hey Travis": the stock model's yes, vetoed
    assert wake.heard(0.96, 0.6) is True  # the stock model's yes, not objected to
    assert wake.heard(0.05, 0.99) is True  # an accent the stock model barely hears
    assert wake.heard(0.3, 0.5) is False
    assert wake.heard(0.6, None) is True and wake.heard(0.4, None) is False  # without it: as before
    second = wake.load()
    assert second is not None and 0.0 <= second.probability(np.zeros((1, 16, 96), dtype=np.float32)) <= 1.0


def test_the_listener_asks_the_second_opinion_only_for_hey_jarvis():
    class Pre:
        def get_features(self, n):
            return np.zeros((1, n, 96), dtype=np.float32)

    class Stock:
        preprocessor = Pre()

        def __init__(self, score):
            self.score = score

        def predict(self, frame):
            return {"hey_jarvis": self.score}

    class Second:
        def __init__(self, p):
            self.p, self.asked = p, 0

        def probability(self, features):
            self.asked += 1
            return self.p

    listener, _ = _listener()
    listener._model, listener._second = Stock(0.97), Second(0.0)
    assert listener.heard_wake_word(QUIET, speaking=False) is False  # "Hey Travis"
    listener._model, listener._second = Stock(0.0), Second(1.0)
    assert listener.heard_wake_word(QUIET, speaking=False) is False and listener._second.asked == 0  # silence: not asked
    listener._model, listener._second = Stock(0.2), Second(0.99)
    assert listener.heard_wake_word(QUIET, speaking=False) is True
    listener._second = None
    assert listener.heard_wake_word(QUIET, speaking=False) is False  # a custom wake word: the threshold alone


def test_when_the_wake_word_cant_load_it_says_so_once_and_tries_again_later(monkeypatch):
    import sys

    from custodian_desktop import voice

    states = []
    listener = Listener(lambda *a: None, states.append, wake_word=True)
    monkeypatch.setitem(sys.modules, "openwakeword", None)  # "DLL load failed ... blocked this file"
    listener._load_model()
    listener._load_model()  # within the retry window: not tried again, and not said again
    assert listener._model is None and states == ["wake word unavailable"]
    monkeypatch.delitem(sys.modules, "openwakeword")
    monkeypatch.setattr(voice, "WAKE_RETRY_SECONDS", 0)
    listener._load_model()  # later, and it works
    assert listener._model is not None and states[-1] == "wake word back"
    assert not any(m.startswith("sklearn") for m in sys.modules if m.startswith("sklearn.metrics._pairwise_fast"))
