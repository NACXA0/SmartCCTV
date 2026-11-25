"""通行窗口归并与截断。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.db import Database
from server.passages import PASSAGE_CAP_SEC, PassageCollector


@pytest.mark.asyncio
async def test_passage_merge_and_stop(tmp_path: Path):
    db = Database(tmp_path / "t.sqlite3")
    await db.init()
    collector = PassageCollector(db)

    started = await collector.start("cmd-1", 30)
    assert started["seconds"] == 30
    assert started["truncated_cap"] is False

    collector.observe(10, "a.jpg")
    collector.observe(10, "b.jpg")  # 同人去重
    collector.observe(None, "s1.jpg")
    await asyncio.sleep(2.1)
    collector.observe(None, "s2.jpg")
    collector.observe(11, "")

    summary = await collector.stop("cmd-1")
    assert summary is not None
    assert summary["command_id"] == "cmd-1"
    assert summary["people_count"] == 4  # 2 known + 2 strangers
    assert summary["stranger_count"] == 2
    assert summary["first_snapshot_path"] == "a.jpg"
    assert summary["truncated"] is False

    listed = await collector.list_passages()
    assert len(listed) == 1
    by = await collector.get_by_command("cmd-1")
    assert by["people_count"] == 4
    await db.close()


@pytest.mark.asyncio
async def test_passage_cap_truncates_after_window(tmp_path: Path):
    db = Database(tmp_path / "t2.sqlite3")
    await db.init()
    collector = PassageCollector(db)

    started = await collector.start("cmd-cap", PASSAGE_CAP_SEC + 40)
    assert started["seconds"] == PASSAGE_CAP_SEC
    assert started["truncated_cap"] is True

    # 提前 stop：门已关，不算截断
    early = await collector.stop("cmd-cap")
    assert early["truncated"] is False

    # 请求超过上限，窗口自然结束 → truncated
    await collector.start("cmd-full", PASSAGE_CAP_SEC + 1)
    collector.observe(7, "z.jpg")
    # 缩短休眠：取消原任务并手动非 early finalize
    async with collector._lock:
        task = collector._tasks.get("cmd-full")
        if task:
            task.cancel()
        row = await collector._finalize_locked("cmd-full", early=False)
    assert row is not None
    assert bool(row.truncated) is True
    assert row.people_count == 1
    assert row.first_snapshot_path == "z.jpg"

    by = await collector.get_by_command("cmd-full")
    assert by["truncated"] is True
    await db.close()
