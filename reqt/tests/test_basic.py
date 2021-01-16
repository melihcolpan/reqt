#!/usr/bin/python
# -*- coding: utf-8 -*-

import pytest

import reqt


async def custom(response):
    print(f"Status Code: {response.status}, Content: {response.content}")


@pytest.mark.asyncio
async def test_async_requestor():
    urls = ["https://www.google.com"]
    await reqt.fetch_all(urls=urls, method=custom)
