#!/usr/bin/python
# -*- coding: utf-8 -*-

import asyncio
import logging
import socket

from aiohttp import ClientConnectorError, ClientSession, TCPConnector

from .helpers import get_request_method


class Reqt:
    def __init__(
        self, urls, method, headers=None, request_type="GET", semaphore_limit=500
    ):
        self.urls = urls
        self.method = method
        self.headers = headers
        self.request_type = request_type
        self.semaphore_limit = semaphore_limit

    async def fetch(self, session, url):
        request_method = get_request_method(session, self.request_type)

        try:
            async with request_method(url, headers=self.headers) as response:
                await self.method(response) if asyncio.iscoroutinefunction(
                    self.method
                ) else self.method(response)

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
    urls, method, headers=None, request_type="GET", semaphore_limit=500
):
    reqt = Reqt(
        urls=urls,
        method=method,
        headers=headers,
        request_type=request_type,
        semaphore_limit=semaphore_limit,
    )

    await reqt.fetch_all()
