from pydantic import SecretStr

from clipforge.config import Settings
from clipforge.doctor import (
    GIB,
    Report,
    check_disk,
    check_keys,
    default_render_workers,
    parse_filters,
)

FILTERS = """Filters:
  T.. = Timeline support
 ... loudnorm          A->A       EBU R128 loudness normalization
 T.C subtitles         V->V       Render text subtitles
 .S tonemap            V->V       Conversion to/from different dynamic ranges.
"""


def test_parse_filters():
    names = parse_filters(FILTERS)
    assert {"loudnorm", "subtitles", "tonemap"} <= names
    assert "zscale" not in names


def test_render_workers_scale_with_ram():
    assert default_render_workers(8 * GIB) == 1
    assert default_render_workers(16 * GIB) == 2
    assert default_render_workers(32 * GIB) == 3


def test_missing_keys_warn_and_never_print_values(tmp_path):
    r = Report()
    check_keys(
        r, Settings(ANTHROPIC_API_KEY=SecretStr("sk-ant-secret"), HF_TOKEN=None, DATA_DIR=tmp_path)
    )
    joined = " ".join(c.detail for c in r.checks)
    assert "sk-ant-secret" not in joined
    assert {c.name: c.status for c in r.checks} == {"ANTHROPIC_API_KEY": "ok", "HF_TOKEN": "warn"}


def test_disk_check_handles_nonexistent_data_dir(tmp_path):
    r = Report()
    check_disk(r, Settings(DATA_DIR=tmp_path / "does" / "not" / "exist"))
    assert r.checks[0].name == "disk"
