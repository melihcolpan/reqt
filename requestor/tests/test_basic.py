#!/usr/bin/python
# -*- coding: utf-8 -*-

import pytest

from requestor.core import Requestor


async def custom(response):
    print(f"Status Code: {response.status}, Content: {response.content}")


@pytest.mark.asyncio
async def test_async_requestor():
    urls = ["https://www.google.com"]
    await Requestor(urls=urls, method=custom).fetch_all()
