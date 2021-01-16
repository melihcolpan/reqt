#!/usr/bin/python
# -*- coding: utf-8 -*-

from aiohttp import ClientSession


def get_request_method(session: ClientSession, method: str):
    return {
        "GET": session.get,
        "POST": session.post,
        "PUT": session.put,
        "DELETE": session.delete,
        "HEAD": session.head,
        "OPTIONS": session.options,
        "PATCH": session.patch,
    }.get(method.upper())
