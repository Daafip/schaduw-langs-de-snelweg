import datetime as dt
from types import SimpleNamespace

from shaduw_langs_de_snelweg.imagery import group_items_by_season, needs_boa_offset


def fake_item(month, cloud=10.0, year=2025, **props):
    return SimpleNamespace(
        datetime=dt.datetime(year, month, 15, 10, 30, tzinfo=dt.timezone.utc),
        properties={"eo:cloud_cover": cloud, **props},
    )


def test_group_items_by_season_buckets_months():
    items = [fake_item(m) for m in range(1, 13)]
    buckets = group_items_by_season(items, max_per_season=12)
    assert {s: len(v) for s, v in buckets.items()} == {
        "djf": 3,
        "mam": 3,
        "jja": 3,
        "son": 3,
    }
    assert all(i.datetime.month in (12, 1, 2) for i in buckets["djf"])


def test_group_items_prefers_least_cloudy_and_caps():
    items = [fake_item(7, cloud=c) for c in (25.0, 5.0, 15.0, 1.0)]
    buckets = group_items_by_season(items, max_per_season=2)
    clouds = [i.properties["eo:cloud_cover"] for i in buckets["jja"]]
    assert clouds == [1.0, 5.0]


def test_needs_boa_offset_new_baseline():
    item = fake_item(6, **{"s2:processing_baseline": "05.00"})
    assert needs_boa_offset(item)


def test_needs_boa_offset_old_baseline():
    item = fake_item(6, **{"s2:processing_baseline": "03.01"})
    assert not needs_boa_offset(item)


def test_needs_boa_offset_already_applied():
    item = fake_item(
        6,
        **{"s2:processing_baseline": "05.00", "earthsearch:boa_offset_applied": True},
    )
    assert not needs_boa_offset(item)


def test_needs_boa_offset_missing_metadata():
    assert not needs_boa_offset(fake_item(6))
