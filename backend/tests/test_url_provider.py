import pytest

from clipforge.errors import DownloadError
from clipforge.providers.url import classify_error, format_selector, parse_info, parse_progress


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("ERROR: [youtube] x: Private video. Sign in", "private"),
        ("Sign in to confirm your age. This video may be inappropriate", "age-restricted"),
        ("This video is available to this channel's members", "members"),
        ("The uploader has not made this video available in your country", "region"),
        ("ERROR: Video unavailable", "unavailable"),
        ("HTTP Error 429: Too Many Requests", "rate-limiting"),
        ("ERROR: Unsupported URL: https://x", "not supported"),
        ("ERROR: unable to extract nsig function", "out of date"),
    ],
)
def test_error_classification_is_actionable(text, needle):
    err = classify_error(text)
    assert needle in err.message.lower() and err.action


def test_unknown_error_offers_update_action():
    assert "Update yt-dlp" in classify_error("boom").action


def test_live_streams_rejected_with_exact_message():
    with pytest.raises(DownloadError, match="Wait until the stream ends and becomes a replay"):
        parse_info("https://x", {"id": "a", "title": "t", "live_status": "is_live"})


def test_playlist_takes_first_entry_with_note_and_harvests_signals():
    info = {
        "_type": "playlist",
        "entries": [
            {
                "id": "v1", "title": "T", "channel": "Ch", "duration": 61, "tags": ["a"],
                "chapters": [{"start_time": 0, "end_time": 10, "title": "Intro"}],
                "heatmap": [{"start_time": 0, "end_time": 5, "value": 0.9}],
                "formats": [{"height": 720}, {"height": 1080}, {"height": None}],
                "was_live": True, "subtitles": {"live_chat": [{}]},
            }
        ],
    }  # fmt: skip
    p = parse_info("https://x", info)
    assert p.note and "first video" in p.note
    assert p.heights == [1080, 720]
    assert p.platform.heatmap and p.platform.chapters and p.platform.chat_replay


def test_progress_line_parsing():
    assert parse_progress("CFP|500|1000|NA|2048.5|3") == {
        "bytes": 500.0, "total": 1000.0, "speed": 2048.5, "eta": 3.0, "pct": 0.5,
    }  # fmt: skip
    assert parse_progress("CFP|500|NA|2000|NA|NA")["pct"] == 0.25  # type: ignore[index]
    assert parse_progress("[download] noise") is None


def test_format_selector_caps_height():
    assert "height<=1080" in format_selector(1080)
    assert "height<=2160" in format_selector(2160)


@pytest.mark.live
def test_live_resolve_real_youtube():
    from clipforge.providers.url import resolve

    p = resolve("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert p.title and p.duration and 15 < p.duration < 30 and p.heights
