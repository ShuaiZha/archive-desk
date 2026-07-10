from pathlib import Path

import pytest

from archivedesk.exporter import _utc_date, ensure_within, safe_filename


def test_safe_filename_blocks_traversal_and_windows_device_names() -> None:
    traversal = safe_filename("../../secrets.txt")
    assert not traversal.startswith(".")
    assert "/" not in traversal
    assert "\\" not in traversal
    assert safe_filename("CON") == "_CON"
    assert safe_filename("a:b?.txt") == "a_b_.txt"
    assert "/" not in safe_filename("folder/name.pdf")
    assert "\\" not in safe_filename("folder\\name.pdf")


def test_ensure_within_rejects_escape(tmp_path: Path) -> None:
    assert ensure_within(tmp_path, tmp_path / "child") == (tmp_path / "child").resolve()
    with pytest.raises(ValueError):
        ensure_within(tmp_path, tmp_path.parent / "outside")


def test_calendar_range_uses_explicit_iana_timezone() -> None:
    assert _utc_date("2026-07-10", time_zone="Asia/Shanghai").isoformat() == (
        "2026-07-09T16:00:00+00:00"
    )
    assert _utc_date(
        "2026-07-10", time_zone="Asia/Shanghai", exclusive_end=True
    ).isoformat() == "2026-07-10T16:00:00+00:00"
