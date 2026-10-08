import asyncio

import aiohttp
import pytest

import reqstorm


async def _rotate(server):
    async with aiohttp.ClientSession() as session:
        async with session.post(server + "/rotate") as response:
            return await response.text()


async def test_static_token_is_sent(server):
    auth = reqstorm.BearerAuth("token-1")
    results = await reqstorm.fetch_all([server + "/protected"], auth=auth)
    assert results[0].text() == "secret"


async def test_401_triggers_one_refresh_shared_by_concurrent_requests(server):
    await _rotate(server)  # token-1 is now expired
    calls = []

    async def refresh():
        calls.append(1)
        await asyncio.sleep(0.05)
        return "token-2"

    auth = reqstorm.BearerAuth("token-1", refresh=refresh)
    results = await reqstorm.fetch_all([server + "/protected"] * 20, auth=auth, concurrency=20)
    assert all(result.text() == "secret" for result in results)
    assert len(calls) == 1 and auth.refreshes == 1 and auth.token == "token-2"
    assert all([a.status for a in r.history] in ([200], [401, 200]) for r in results)


async def test_token_is_fetched_before_the_first_request(server):
    auth = reqstorm.BearerAuth(refresh=lambda: "token-1")
    results = await reqstorm.fetch_all([server + "/protected"], auth=auth)
    assert results[0].ok and auth.refreshes == 1


async def test_a_second_401_is_returned_not_looped(server):
    auth = reqstorm.BearerAuth("wrong", refresh=lambda: "still-wrong")
    results = await reqstorm.fetch_all([server + "/protected"], auth=auth)
    assert results[0].status == 401 and results[0].attempts == 2


async def test_without_refresh_a_401_is_just_a_result(server):
    results = await reqstorm.fetch_all([server + "/protected"], auth=reqstorm.BearerAuth("wrong"))
    assert results[0].status == 401 and results[0].attempts == 1


async def test_custom_header_and_scheme(server):
    auth = reqstorm.BearerAuth("abc", header="X-Api-Key", scheme="")
    results = await reqstorm.fetch_all([server + "/echo"], auth=auth)
    assert results[0].json()["headers"]["X-Api-Key"] == "abc"


def test_auth_needs_a_token_or_a_refresh_function():
    with pytest.raises(ValueError):
        reqstorm.BearerAuth()


async def test_refresh_must_return_a_string(server):
    auth = reqstorm.BearerAuth(refresh=lambda: None)
    results = await reqstorm.fetch_all([server + "/protected"], auth=auth)
    assert isinstance(results[0].error, ValueError)


def test_sync_api_with_refresh(thread_server):
    auth = reqstorm.BearerAuth("expired", refresh=lambda: "token-1")
    results = reqstorm.fetch_all_sync([thread_server + "/protected"] * 3, auth=auth)
    assert [r.text() for r in results] == ["secret"] * 3
