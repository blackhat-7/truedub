import numpy as np
import pytest

from truedub import tts
from truedub.pipeline import (
    SR,
    frame_db,
    holes,
    is_junk,
    merge_ranges,
    merge_segments,
    next_chunk,
    pick_language,
    quietest,
    split_long,
)


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def asr_seg(text, avg_logprob=-0.3, no_speech_prob=0.05, compression_ratio=1.4):
    return {
        "text": text,
        "avg_logprob": avg_logprob,
        "no_speech_prob": no_speech_prob,
        "compression_ratio": compression_ratio,
    }


def speech(seconds, pauses=()):
    """Loudness frames for noisy 'speech' with silent pauses at the given times."""
    audio = np.random.default_rng(0).uniform(-0.3, 0.3, int(seconds * SR)).astype(np.float32)
    for t in pauses:
        audio[int(t * SR) : int((t + 0.5) * SR)] = 0
    return frame_db(audio)


def test_quietest_finds_pause():
    assert 40 <= quietest(speech(60, [40]), 30, 45) <= 40.5


def test_merge_ranges_and_holes():
    assert merge_ranges([(30, 45), (0, 15), (15, 30), (60, 70)]) == [(0, 45), (60, 70)]
    assert holes([(0, 45), (60, 70)], 100) == [(45, 60), (70, 100)]
    assert holes([(10, 100)], 100) == [(0, 10)]
    assert holes([(0, 100)], 100) == []


def test_next_chunk_starts_short_then_grows_at_pauses():
    db = speech(300, [12, 50, 100])
    first = next_chunk(db, [], 0, -1, {})
    assert first[0] == 0 and 12 <= first[1] <= 12.5  # short: pause within 10..15 s
    second = next_chunk(db, [first], 0, first[1], {})
    assert second[0] == first[1] and 50 <= second[1] <= 50.5  # long: pause within 42..57 s


def test_next_chunk_seek_starts_at_pause_before_playhead():
    db = speech(300, [199])
    chunk = next_chunk(db, [(0, 30)], 200, 30, {})
    assert 199 <= chunk[0] <= 199.5
    assert chunk[1] - chunk[0] <= 15


def test_next_chunk_skips_done_and_wraps_to_earlier_holes():
    db = speech(100)
    assert next_chunk(db, [(0, 30), (50, 100)], 60, 100, {})[0] == 30  # nothing ahead: wrap
    assert next_chunk(db, [(0, 30)], 10, 30, {})[0] == 30  # playhead in done range
    assert next_chunk(db, [(0, 100)], 10, 100, {}) is None
    assert next_chunk(db, [(0, 90)], 0, 90, {}) == (90, 100)  # short tail taken whole


def test_next_chunk_reuses_cached_asr_chunk():
    db = speech(100)
    assert next_chunk(db, [(0, 30)], 0, -1, {30.0: 70.0}) == (30, 70)
    assert next_chunk(db, [(0, 30), (60, 100)], 0, -1, {30.0: 70.0})[1] <= 60  # would overlap


def test_pick_language_never_picks_english():
    hinglish = [{"en": 0.6, "hi": 0.3, "ur": 0.1}, {"en": 0.5, "hi": 0.4, "ur": 0.1}]
    assert pick_language(hinglish) == "hi"


def test_pick_language_sums_samples():
    probs = [{"es": 0.9, "pt": 0.1}, {"es": 0.2, "pt": 0.5}, {"es": 0.4, "pt": 0.4}]
    assert pick_language(probs) == "es"


@pytest.mark.parametrize(
    "seg",
    [
        asr_seg("Thanks for watching!"),
        asr_seg("Please subscribe to my channel."),
        asr_seg("Subtitles by the Amara.org community"),
        asr_seg("you"),
        asr_seg("I am going I am going I am going", compression_ratio=3.1),
        asr_seg("Something vague", avg_logprob=-1.3),
        asr_seg("आज हम एक एपीआई बनाएंगे"),
        asr_seg("..."),
    ],
)
def test_is_junk_drops_hallucinations(seg):
    assert is_junk(seg, peak_db=-20)


def test_is_junk_keeps_confident_speech_despite_no_speech_prob():
    assert not is_junk(asr_seg("To store key value pairs, you use a dictionary.", -0.48, 0.87), -14)
    assert not is_junk(asr_seg("There can be random topics I spend time on.", -0.89, 0.71), -14)


def test_is_junk_drops_silent_regions():
    assert is_junk(asr_seg("So today we will build a REST API."), peak_db=-60)


@pytest.mark.parametrize(
    "text",
    ["So today we will build a REST API.", "Thank you for watching, now let's code.", "Café"],
)
def test_is_junk_keeps_real_speech(text):
    assert not is_junk(asr_seg(text), peak_db=-20)


def test_merge_joins_unfinished_sentences():
    segs = [
        seg(0, 2, "So today we will"),
        seg(2.2, 4, "build a REST API."),
        seg(4.1, 7, "First we install Flask."),
    ]
    assert merge_segments(segs) == [
        seg(0, 4, "So today we will build a REST API."),
        seg(4.1, 7, "First we install Flask."),
    ]


def test_merge_joins_tiny_fragments_but_respects_gaps_and_length():
    assert merge_segments([seg(0, 0.8, "Okay."), seg(1.0, 4, "Let's look at the code.")]) == [
        seg(0, 4, "Okay. Let's look at the code.")
    ]
    apart = [seg(0, 2, "So today we"), seg(5, 7, "build an API.")]
    assert merge_segments(apart) == apart
    long = [seg(0, 8, "This is a long clause and"), seg(8.1, 14, "this one makes it too long.")]
    assert merge_segments(long) == long


def test_split_long_splits_at_sentences_by_length():
    s = seg(
        10,
        34,
        "First sentence is here. Second one is about as long. Third goes here too. Fourth ends it.",
    )
    parts = split_long(s)
    assert len(parts) == 2
    assert parts[0]["start"] == 10 and parts[-1]["end"] == 34
    assert parts[0]["end"] == parts[1]["start"]
    assert " ".join(p["text"] for p in parts) == s["text"]
    assert all(p["end"] - p["start"] < 15 for p in parts)


def test_split_long_keeps_short_or_single_sentence():
    assert split_long(seg(0, 5, "One. Two.")) == [seg(0, 5, "One. Two.")]
    assert split_long(seg(0, 30, "one endless sentence")) == [seg(0, 30, "one endless sentence")]


def test_next_speed():
    assert tts.next_speed(1.0, 2.0, 3.0) == 1.0
    assert tts.next_speed(1.0, 3.0, 2.5) == pytest.approx(1.26)
    assert tts.next_speed(1.2, 3.0, 2.5) == pytest.approx(1.512)
    assert tts.next_speed(1.0, 10.0, 2.0) == tts.MAX_SPEED
    assert tts.next_speed(tts.MAX_SPEED, 10.0, 2.0) == tts.MAX_SPEED


def test_fit_clip_trims_silence():
    tone = np.full(tts.SR, 0.5, dtype=np.float32)
    padded = np.concatenate([np.zeros(tts.SR // 2, np.float32), tone, np.zeros(tts.SR, np.float32)])
    assert len(tts.fit_clip(padded, slot=5)) == tts.SR


def test_fit_clip_truncates_with_fade():
    clip = tts.fit_clip(np.full(3 * tts.SR, 0.5, dtype=np.float32), slot=2)
    assert len(clip) == 2 * tts.SR
    assert clip[-1] == pytest.approx(0)
    assert clip[0] == pytest.approx(0.5)
