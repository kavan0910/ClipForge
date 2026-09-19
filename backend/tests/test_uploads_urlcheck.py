import resource

import pytest

from clipforge.errors import DownloadError, MediaError
from clipforge.uploads import OffsetMismatch, UploadStore, sanitize_filename
from clipforge.urlcheck import validate_url


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("../../etc/passwd", "passwd"),
        ("a b?.mp4", "a b_.mp4"),
        ("C:\\x\\y.mkv", "y.mkv"),
        ("..", "upload"),
    ],
)
def test_sanitize(raw, clean):
    assert sanitize_filename(raw) == clean


def test_upload_roundtrip_and_resume(tmp_path):
    s = UploadStore(tmp_path)
    m = s.create("clip.mp4", 10)
    s.append(m.id, 0, [b"abcd"])
    assert s.status(m.id).offset == 4
    with pytest.raises(OffsetMismatch) as e:
        s.append(m.id, 0, [b"zz"])
    assert e.value.expected == 4
    s.append(m.id, 4, [b"efghij"])
    assert s.status(m.id).complete
    dest_dir = tmp_path / "proj"
    dest_dir.mkdir()
    path, original = s.take(m.id, dest_dir)
    assert path.read_bytes() == b"abcdefghij" and original == "clip.mp4"


def test_upload_rejects_bad_type_overflow_and_ids(tmp_path):
    s = UploadStore(tmp_path)
    with pytest.raises(MediaError):
        s.create("x.exe", 10)
    m = s.create("a.mp4", 3)
    with pytest.raises(MediaError, match="more data"):
        s.append(m.id, 0, [b"abcd"])
    for bad in ["../x", "zz", ""]:
        with pytest.raises(MediaError):
            s.status(bad)


def test_upload_resume_after_crash_uses_data_file_size(tmp_path):
    s = UploadStore(tmp_path)
    m = s.create("a.mp4", 10)
    (tmp_path / m.id / "data.partial").write_bytes(b"12345")  # meta.json still says 0
    assert s.status(m.id).offset == 5


def test_large_streamed_upload_keeps_memory_flat(tmp_path):
    """Stream 3 GiB of zero chunks (sparse-friendly) and check peak RSS growth stays small."""
    s = UploadStore(tmp_path)
    total, chunk = 3 * 1024**3, 8 * 1024**2
    m = s.create("big.mp4", total)
    zero = bytes(chunk)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    s.append(m.id, 0, (zero for _ in range(total // chunk)))
    growth_mib = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 2**20
    assert s.status(m.id).complete
    assert growth_mib < 100
    s.cancel(m.id)


@pytest.mark.parametrize(
    "url",
    ["ftp://x.com/a", "file:///etc/passwd", "javascript:alert(1)", "https://u:p@example.com/"],
)
def test_url_rejects_bad_schemes_and_credentials(url):
    with pytest.raises(DownloadError):
        validate_url(url, allow_private=True)


def test_url_rejects_private_targets():
    for url in [
        "http://127.0.0.1/x",
        "http://localhost:8000/",
        "http://192.168.1.5/",
        "http://[::1]/",
    ]:
        with pytest.raises(DownloadError, match="private"):
            validate_url(url)
