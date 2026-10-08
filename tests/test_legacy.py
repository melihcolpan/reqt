import logging

import pytest

import reqstorm


async def test_v1_call_still_works_with_a_warning(server):
    statuses = []

    async def custom_method(response):
        statuses.append((response.status, await response.text()))

    with pytest.warns(DeprecationWarning, match="1.x API"):
        result = await reqstorm.fetch_all(urls=[f"{server}/ok"] * 3, method=custom_method)
    assert result is None
    assert statuses == [(200, "ok")] * 3


async def test_v1_positional_sync_callback_and_keywords(server):
    seen = []
    with pytest.warns(DeprecationWarning):
        await reqstorm.fetch_all(
            [f"{server}/echo"],
            lambda response: seen.append(response.method),
            request_type="post",
            semaphore_limit=2,
        )
    assert seen == ["POST"]


async def test_v1_failures_are_logged_not_raised(server, caplog):
    seen = []
    with caplog.at_level(logging.ERROR, logger="reqstorm"), pytest.warns(DeprecationWarning):
        await reqstorm.fetch_all(
            [f"{server}/disconnect", f"{server}/ok"], method=lambda r: seen.append(r.status)
        )
    assert seen == [200]
    assert "disconnect" in caplog.text


async def test_v1_class(server):
    seen = []
    with pytest.warns(DeprecationWarning):
        await reqstorm.Reqt([f"{server}/ok"], lambda r: seen.append(r.status)).fetch_all()
    assert seen == [200]


async def test_v1_verifies_certificates_now(tls_server, caplog):
    seen = []
    with caplog.at_level(logging.ERROR, logger="reqstorm"), pytest.warns(DeprecationWarning):
        await reqstorm.fetch_all([f"{tls_server}/ok"], method=lambda r: seen.append(r.status))
    assert seen == [] and "certificate" in caplog.text.lower()


async def test_legacy_keywords_without_callback_are_rejected(server):
    with pytest.raises(TypeError, match="1.x API"):
        await reqstorm.fetch_all([f"{server}/ok"], request_type="POST")


async def test_unknown_keywords_are_rejected(server):
    with pytest.raises(TypeError, match="unexpected"):
        await reqstorm.fetch_all([f"{server}/ok"], nonsense=1)
