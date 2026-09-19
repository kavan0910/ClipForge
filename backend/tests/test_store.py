import pytest

from clipforge.store import Project, canonical_hash


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_stage_cache_hit_and_invalidation(tmp_path):
    p = Project.create(tmp_path, "abc")
    calls = []

    def fn():
        calls.append(1)
        return ["out.txt"]

    _, cached = p.run_stage("s", 1, "in1", {"x": 1}, fn)
    assert not cached
    _, cached = p.run_stage("s", 1, "in1", {"x": 1}, fn)
    assert cached and len(calls) == 1
    for args in [(1, "in2", {"x": 1}), (1, "in1", {"x": 2}), (2, "in1", {"x": 1})]:
        _, cached = p.run_stage("s", *args, fn)
        assert not cached
    assert len(calls) == 4


def test_stage_reruns_when_outputs_missing(tmp_path):
    p = Project.create(tmp_path, "abc")
    p.run_stage("s", 1, "i", {}, lambda: [])
    _, cached = p.run_stage("s", 1, "i", {}, lambda: [], outputs_exist=lambda: False)
    assert not cached


def test_failed_stage_leaves_no_record(tmp_path):
    p = Project.create(tmp_path, "abc")

    def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        p.run_stage("s", 1, "i", {}, boom)
    assert p.read_stage("s") is None


def test_path_traversal_and_bad_ids_rejected(tmp_path):
    p = Project.create(tmp_path, "abc")
    with pytest.raises(ValueError):
        p.path("..", "other")
    for bad in ["", "../x", "A B", "a/b"]:
        with pytest.raises(ValueError):
            Project.open(tmp_path, bad)


def test_events_are_incremental_and_ignore_partial_lines(tmp_path):
    p = Project.create(tmp_path, "abc")
    p.emit("progress", stage="ingest", pct=0.5)
    ev, off = p.read_events(0)
    assert ev[0]["stage"] == "ingest"
    p.emit("done")
    with p.events_file.open("a") as f:
        f.write('{"type": "part')  # unfinished write
    ev2, off2 = p.read_events(off)
    assert [e["type"] for e in ev2] == ["done"]
    assert p.read_events(off2)[0] == []


def test_delete_removes_everything_and_never_follows_symlinks(tmp_path):
    big = tmp_path / "outside.bin"
    big.write_bytes(b"keep me")
    p = Project.create(tmp_path / "projects", "abc")
    (p.root / "source" / "master.link").symlink_to(big)
    p.delete()
    assert not p.root.exists()
    assert big.read_bytes() == b"keep me"
