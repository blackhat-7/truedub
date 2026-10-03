import io
import math
from itertools import pairwise

import numpy as np
import pytest
from pydantic import ValidationError
from yt_dlp.cookies import YoutubeDLCookieJar

from truedub import tts
from truedub.app import Transcript
from truedub.pipeline import (
    GAP,
    MAX_LAG,
    MAX_SPEED,
    MILD,
    SR,
    build_sentences,
    frame_db,
    holes,
    is_junk,
    merge_ranges,
    merge_segments,
    netscape_cookies,
    next_chunk,
    next_text_chunk,
    pick_language,
    place,
    quietest,
    split_long,
    video_context,
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


def test_speak_trims_silence(monkeypatch):
    tone = np.full(tts.SR, 0.5, dtype=np.float32)
    padded = np.concatenate([np.zeros(tts.SR // 2, np.float32), tone, np.zeros(tts.SR, np.float32)])
    monkeypatch.setattr(tts, "synthesize", lambda text, voice, speed=1.0: padded)
    assert len(tts.speak("Hi.", "am_michael")) == tts.SR


def test_fit_clip_keeps_clip_that_fits():
    clip = np.full(tts.SR, 0.5, dtype=np.float32)
    assert len(tts.fit_clip(clip, slot=2)) == tts.SR


def test_fit_clip_cuts_at_last_pause():
    word = np.full(tts.SR // 2, 0.5, dtype=np.float32)
    pause = np.zeros(tts.SR // 5, dtype=np.float32)
    clip = tts.fit_clip(np.concatenate([word, pause, word, pause, word]), slot=1.6)
    assert len(clip) == int(1.2 * tts.SR)  # the first two words, not part of the third


def test_fit_clip_truncates_with_fade_without_pause():
    clip = tts.fit_clip(np.full(3 * tts.SR, 0.5, dtype=np.float32), slot=2)
    assert len(clip) == 2 * tts.SR
    assert clip[-1] == pytest.approx(0)
    assert clip[0] == pytest.approx(0.5)


# The start of I7_eVcsOXik's English captions as YouTube sends them: fragments whose times
# overlap the next one, blank events, and two sentences in one fragment.
CAPTIONS = [
    seg(0.0, 3.84, "Hello everyone, in this video I am going to"),
    seg(2.07, 3.84, "\n"),
    seg(2.08, 6.319, "tell you about a project"),
    seg(3.83, 6.319, "\n"),
    seg(3.84, 8.16, "which is Docine."),
    seg(6.309, 8.16, "\n"),
    seg(6.319, 11.04, "We are also using this Docine project practically"),
    seg(8.15, 11.04, "\n"),
    seg(8.16, 13.12, "for internal work and"),
    seg(11.03, 13.12, "\n"),
    seg(11.04, 14.719, "I will not just tell you about this project that brother, this is a"),
    seg(13.11, 14.719, "\n"),
    seg(13.12, 16.64, "project.  You can create such projects to"),
]


def test_build_sentences_joins_fragments_and_splits_at_punctuation():
    shuffled = CAPTIONS[::2] + CAPTIONS[1::2]
    assert build_sentences(shuffled, 100) == [
        seg(
            0.0,
            6.32,
            "Hello everyone, in this video I am going to tell you about a project which is Docine.",
        ),
        seg(
            6.32,
            13.89,  # "project." takes its share of the fragment by length
            "We are also using this Docine project practically for internal work and "
            "I will not just tell you about this project that brother, this is a project.",
        ),
        seg(13.89, 16.64, "You can create such projects to"),
    ]


def test_build_sentences_splits_at_pauses_and_drops_tags():
    captions = [
        seg(0, 2, "so we start"),
        seg(1, 3, "[Music]"),
        seg(4, 6, "♪ ♪"),
        seg(6, 8, "and then  we  go on ."),
    ]
    assert build_sentences(captions, 100) == [
        seg(0, 1, "so we start"),
        seg(6, 8, "and then we go on."),
    ]


def test_build_sentences_caps_length_after_a_comma():
    text = "one two three four five six seven eight, nine ten eleven twelve 13 14"
    words = text.split(" ")
    captions = [seg(i, i + 1, w) for i, w in enumerate(words)]
    assert build_sentences(captions, 100) == [
        seg(0, 8, "one two three four five six seven eight,"),
        seg(8, 14, "nine ten eleven twelve 13 14"),
    ]


def test_build_sentences_clips_to_duration():
    captions = [seg(0, 5, "Hello there."), seg(9, 12, "Too late.")]
    assert build_sentences(captions, 4) == [seg(0, 4, "Hello there.")]


def test_transcript_validation():
    good = {"language": "hi", "duration": 60, "segments": [seg(0, 2, "Hi."), seg(2, 2, "\n")]}
    assert Transcript.model_validate(good).duration == 60
    bad = [
        {**good, "duration": float("nan")},
        {**good, "duration": 0},
        {**good, "segments": []},
        {**good, "segments": [seg(0, 2, " ")]},
        {**good, "segments": [seg(3, 2, "Hi.")]},
        {**good, "segments": [seg(-1, 2, "Hi.")]},
        {**good, "segments": [seg(0, float("inf"), "Hi.")]},
        {**good, "segments": [seg(0, 2, "x" * 2000)]},
    ]
    for data in bad:
        with pytest.raises(ValidationError):
            Transcript.model_validate(data)


def test_next_text_chunk_cuts_at_sentence_starts_and_covers_gaps():
    starts = [1.0, 5.0, 12.0, 20.0, 48.0, 70.0]
    first = next_text_chunk(starts, 100, [], 0, -1)
    assert first == (0, 12.0)  # short, and starts at 0 so the timeline is covered
    second = next_text_chunk(starts, 100, [first], 0, first[1])
    assert second == (12.0, 20.0)  # long, but 48 would make it longer than 30 s
    assert next_text_chunk(starts, 100, [(0, 70)], 0, 70) == (70, 100)  # tail taken whole


def test_next_text_chunk_starts_at_playhead_sentence():
    starts = [1.0, 5.0, 12.0, 20.0, 48.0, 70.0]
    assert next_text_chunk(starts, 100, [(0, 12)], 50, 12) == (48.0, 70.0)
    assert next_text_chunk(starts, 100, [(0, 12), (48, 100)], 50, 100) == (12, 20)  # wrap
    assert next_text_chunk(starts, 100, [(0, 100)], 0, 100) is None
    assert next_text_chunk([], 100, [], 0, -1) == (0, 100)  # no speech at all


def schedule(starts, lengths, end):
    """Place clips one after another as pipeline._process does, with exact speed-ups.

    Returns (start, speed, played length) per clip."""
    out, free = [], -math.inf
    for i, (start, length) in enumerate(zip(starts, lengths)):
        next_start = starts[i + 1] if i + 1 < len(starts) else None
        start, speed, latest = place(start, free, length, next_start, end)
        played = min(length / speed, latest - start)
        out.append((start, speed, round(played, 3)))
        free = start + played + GAP
    return out


def test_place_keeps_clips_that_fit_on_time():
    assert schedule([0, 5, 10], [3, 3, 3], 15) == [(0, 1.0, 3), (5, 1.0, 3), (10, 1.0, 3)]


def test_place_speeds_up_mildly_then_lags_and_catches_up():
    plan = schedule([0, 2, 6], [2.1, 2.0, 1.0], 10)
    assert plan[0][:2] == (0, round(2.1 / (2 - GAP), 3))  # within MILD: on time
    assert plan[1][:2] == (2, 1.0)
    plan = schedule([0, 2, 8], [3.0, 1.0, 1.0], 10)
    assert plan[0][1] == MILD
    assert plan[1][:2] == (round(3.0 / MILD + GAP, 2), 1.0)  # starts late, at natural speed
    assert plan[2][:2] == (8, 1.0)  # caught up in the pause


def test_place_caps_lag_and_speed_and_never_overlaps():
    starts = [0, 1, 2, 3]
    plan = schedule(starts, [2.0] * 4, 6)
    for (start, speed, _), seg_start in zip(plan, starts):
        assert start - seg_start <= MAX_LAG + 0.01
        assert 1.0 <= speed <= MAX_SPEED
    for (start, _, played), (next_start, _, _) in pairwise(plan):
        assert start + played + GAP <= next_start + 0.01
    start, _, played = plan[-1]
    assert start + played <= 6 + 1e-6  # the chunk's last clip ends by the chunk's end
    assert played < 2.0 / MAX_SPEED  # too much speech for the time: the last resort, cut


def test_place_last_clip_speeds_up_to_end_by_chunk_end():
    assert schedule([8], [2.4], 10) == [(8, 1.2, 2.0)]


def test_netscape_cookies_load_in_yt_dlp():
    text = netscape_cookies(
        [
            {
                "name": "SID",
                "value": "a=b",
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "expires": 1893456000.5,
            },
            {
                "name": "PREF",
                "value": "x",
                "domain": "www.youtube.com",
                "path": "/",
                "secure": False,
                "expires": 0,
            },
        ]
    )
    assert text.splitlines()[1] == ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\ta=b"
    jar = YoutubeDLCookieJar(io.StringIO(text))
    jar.load()
    cookies = {c.name: c for c in jar}
    assert cookies["SID"].value == "a=b" and cookies["SID"].secure
    assert cookies["SID"].expires == 1893456000
    assert cookies["PREF"].domain == "www.youtube.com" and not cookies["PREF"].domain_specified
    assert cookies["PREF"].expires is None  # session cookie


def test_video_context_uses_title_and_first_description_line():
    info = {"title": "Spring AI & RAG", "description": "\nUsing PGVector.\nLinks: https://x.y"}
    assert video_context(info) == "Spring AI & RAG. Using PGVector."
    assert video_context({"title": "T", "description": "word " * 100}).endswith("word")
    assert len(video_context({"title": "T", "description": "word " * 100})) <= 3 + 150
    assert video_context({"title": "T", "description": None}) == "T"
