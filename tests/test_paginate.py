import json
import sqlite3
from contextlib import closing

import pytest

import reqstorm
from reqstorm._client import Request, Result
from reqstorm._paginate import _with_param


def _ids(results):
    return sorted(item["id"] for result in results for item in result.json()["items"])


@pytest.mark.parametrize(
    "path, paginate",
    [
        ("/pages/next", reqstorm.NextLink("links.next")),
        ("/pages/link", reqstorm.LinkHeader()),
        ("/pages/cursor", reqstorm.Cursor("meta.next_cursor", param="after")),
        ("/pages/number", reqstorm.PageNumber(items="items")),
    ],
)
async def test_strategies_follow_every_page(server, path, paginate):
    results = await reqstorm.fetch_all([server + path], paginate=paginate)
    assert _ids(results) == [1, 2, 3, 4, 5, 6, 7]
    assert all(result.ok and result.seed_index == 0 for result in results)
    assert sorted(result.page for result in results) == list(range(len(results)))


async def test_page_number_stops_at_the_first_empty_page(server):
    results = await reqstorm.fetch_all(
        [server + "/pages/number"], paginate=reqstorm.PageNumber(items="items")
    )
    assert len(results) == 4  # 3 + 3 + 1 items, then an empty page
    assert results[-1].json()["items"] == []


async def test_several_starting_urls_are_paginated_independently(server):
    starts = [server + "/pages/next", server + "/pages/next?page=2", server + "/ok"]
    results = await reqstorm.fetch_all(starts, paginate=reqstorm.NextLink("links.next"), concurrency=2)
    by_seed = {seed: [r for r in results if r.seed_index == seed] for seed in range(3)}
    assert len(by_seed[0]) == 3 and len(by_seed[1]) == 2
    assert len(by_seed[2]) == 1  # "ok" is not JSON: no next page
    assert sorted(r.index for r in results) == list(range(6))


async def test_max_pages_stops_a_server_that_always_has_a_next_page(server):
    results = await reqstorm.fetch_all([server + "/pages/loop"], paginate=reqstorm.NextLink(max_pages=5))
    assert len(results) == 5


async def test_a_link_back_to_the_same_page_ends_pagination(server):
    loop = server + "/pages/loop?same=1"
    results = await reqstorm.fetch_all([loop], paginate=reqstorm.NextLink())
    assert len(results) == 1


async def test_failed_pages_are_not_followed(server):
    results = await reqstorm.fetch_all([server + "/status/500"], paginate=reqstorm.NextLink())
    assert len(results) == 1 and results[0].status == 500


async def test_pagination_with_stream(server):
    seen = [r async for r in reqstorm.stream([server + "/pages/link"], paginate=reqstorm.LinkHeader())]
    assert _ids(seen) == [1, 2, 3, 4, 5, 6, 7]


async def test_retry_rounds_and_resume_are_refused(server, tmp_path):
    with pytest.raises(ValueError, match="retry_rounds"):
        await reqstorm.fetch_all([server + "/ok"], paginate=reqstorm.NextLink(), retry_rounds=1)
    with pytest.raises(ValueError, match="resume"):
        await reqstorm.fetch_to_file([server + "/ok"], tmp_path / "out.jsonl", paginate=reqstorm.NextLink(),
                                     resume=True)  # fmt: skip


async def test_pagination_with_schema_and_explode_to_a_database(server, tmp_path):
    schema = {"id": reqstorm.Field("id", int, key=True), "name": reqstorm.Field("name", str)}
    target = tmp_path / "items.db"
    summary = await reqstorm.fetch_to_file(
        [server + "/pages/next"], target, table="items", schema=schema, explode="items",
        paginate=reqstorm.NextLink("links.next"),
    )  # fmt: skip
    assert summary.ok == 3 and summary.rows == 7
    with closing(sqlite3.connect(target)) as connection:
        rows = connection.execute("SELECT id, source_index FROM items ORDER BY id").fetchall()
    assert rows == [(number, 0) for number in range(1, 8)]


async def test_pages_in_a_file_keep_the_starting_position(server, tmp_path):
    target = tmp_path / "pages.jsonl"
    await reqstorm.fetch_to_file([server + "/ok", server + "/pages/cursor"], target,
                                 paginate=reqstorm.Cursor("meta.next_cursor", param="after"))  # fmt: skip
    records = [json.loads(line) for line in target.read_text().splitlines()]
    assert sorted((r["index"], r["page"]) for r in records) == [(0, 0), (1, 0), (1, 1), (1, 2)]


def test_with_param_replaces_the_value_in_the_url_and_params():
    request = Request("https://api.example.com/items?page=3&q=shoes", params={"page": 3, "sort": "new"})
    updated = _with_param(request, "page", 4)
    assert updated.url == "https://api.example.com/items?q=shoes"
    assert updated.params == {"sort": "new", "page": 4}


def test_link_header_parsing_handles_several_relations():
    from multidict import CIMultiDict, CIMultiDictProxy

    link = '<https://api.example.com/x?page=1>; rel="prev", <https://api.example.com/x?page=3>; rel="next a"'
    headers = CIMultiDictProxy(CIMultiDict(Link=link))
    result = Result(Request("https://api.example.com/x?page=2"), index=0, status=200, headers=headers)
    following = reqstorm.LinkHeader().next(result.request, result, 0)
    assert following is not None and following.url == "https://api.example.com/x?page=3"
