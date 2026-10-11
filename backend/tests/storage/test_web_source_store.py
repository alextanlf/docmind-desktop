"""已收录来源的存储：幂等、证据合并不丢、顺序稳定。"""
from __future__ import annotations

import json
from pathlib import Path

from app.storage.database import Database
from app.storage.models import WebSourceRecord, utc_now
from app.storage.repositories import WebSourceStore


def store_for(tmp_path: Path) -> WebSourceStore:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'web-sources.sqlite3'}")
    database.upgrade()
    return WebSourceStore(database)


def test_adding_the_same_domain_twice_updates_instead_of_duplicating(tmp_path: Path) -> None:
    """domain 是主键，所以"再加一次同一个站"天然是更新 —— 表结构保证幂等。"""
    store = store_for(tmp_path)
    store.upsert([WebSourceRecord(domain="docs.python.org", evidence_json='["种子表"]')])
    store.upsert([WebSourceRecord(domain="docs.python.org", evidence_json='["模型提名"]')])

    rows = store.list()

    assert len(rows) == 1
    # 两条理由都留着：一个站可能先因种子表被收录、后来又因提名被加入，
    # 只留最新的一条会让用户下次回来看时以为它只有一个来路。
    assert json.loads(rows[0].evidence_json) == ["种子表", "模型提名"]


def test_partial_updates_never_erase_what_the_seed_table_brought(tmp_path: Path) -> None:
    """只带域名的"手工添加"不该把显示名与抓取入口抹掉。"""
    store = store_for(tmp_path)
    store.upsert(
        [
            WebSourceRecord(
                domain="docs.python.org",
                entry_url="https://docs.python.org/3/",
                label="Python 官方文档",
                kind="official_docs",
                verified=True,
                evidence_json='["种子表"]',
            )
        ]
    )
    store.upsert([WebSourceRecord(domain="docs.python.org")])

    row = store.list()[0]

    assert row.entry_url == "https://docs.python.org/3/"
    assert row.label == "Python 官方文档"
    assert row.kind == "official_docs"
    assert row.verified is True


def test_verified_never_downgrades(tmp_path: Path) -> None:
    """一次"未核对"的重新加入，不该把已核对的来源降级。"""
    store = store_for(tmp_path)
    store.upsert([WebSourceRecord(domain="docs.python.org", verified=True)])
    store.upsert([WebSourceRecord(domain="docs.python.org", verified=False)])

    assert store.list()[0].verified is True


def test_listing_order_is_recent_first_then_domain(tmp_path: Path) -> None:
    """同一批加入的记录时间戳相同，没有次级键的话每次刷新顺序都可能变。"""
    store = store_for(tmp_path)
    stamped = utc_now()
    store.upsert(
        [
            WebSourceRecord(domain="b.example", evidence_json="[]", added_at=stamped),
            WebSourceRecord(domain="a.example", evidence_json="[]", added_at=stamped),
            WebSourceRecord(domain="c.example", evidence_json="[]", added_at=stamped),
        ]
    )

    assert [row.domain for row in store.list()] == ["a.example", "b.example", "c.example"]


def test_removing_reports_whether_a_row_was_actually_there(tmp_path: Path) -> None:
    store = store_for(tmp_path)
    store.upsert([WebSourceRecord(domain="docs.python.org", evidence_json="[]")])

    assert store.remove("docs.python.org") is True
    assert store.remove("docs.python.org") is False
    assert store.list() == []
