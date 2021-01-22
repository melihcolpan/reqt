#!/usr/bin/python
# -*- coding: utf-8 -*-

import asyncio
import logging
import socket

from aiohttp import ClientConnectorError, ClientSession, TCPConnector

from helpers import get_request_method


class Reqt:
    def __init__(
        self, urls, func, headers=None, method="GET", semaphore_limit=500
    ):
        self.urls = urls
        self.func = func
        self.headers = headers
        self.method = method
        self.semaphore_limit = semaphore_limit

    async def fetch(self, session, url):
        request_method = get_request_method(session, self.method)

        try:
            async with request_method(url, headers=self.headers) as response:
                await self.func(response) if asyncio.iscoroutinefunction(
                    self.func
                ) else self.func(response)

        except ClientConnectorError as e:
            logging.exception(e)

    async def bound_fetch(self, sem, session, url) -> fetch:
        async with sem:
            return await self.fetch(session, url)

    async def fetch_all(self):
        connector = TCPConnector(limit=0, ssl=False, family=socket.AF_INET)
        async with ClientSession(connector=connector) as session:
            sem = asyncio.Semaphore(self.semaphore_limit)

            tasks = [
                asyncio.create_task(self.bound_fetch(sem, session, url))
                for url in self.urls
            ]
            [await task for task in asyncio.as_completed(tasks)]


async def fetch_all(
    urls, func, headers=None, method="GET", semaphore_limit=500
):
    reqt = Reqt(
        urls=urls,
        func=func,
        headers=headers,
        method=method,
        semaphore_limit=semaphore_limit,
    )

    await reqt.fetch_all()
