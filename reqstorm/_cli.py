"""The ``reqstorm`` command: send requests from a file of URLs or a CSV, without writing Python."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

from . import __version__
from ._auth import BearerAuth
from ._cache import Cache
from ._client import Request
from ._limits import parse_rate
from ._paginate import Cursor, LinkHeader, NextLink, PageNumber, Paginator
from ._plan import estimate
from ._schema import Field, Schema, SchemaError, infer_schema
from ._sync import fetch_all_sync, fetch_to_file_sync, stream_sync
from ._template import from_template, read_csv

EXAMPLES = """\
examples:
  reqstorm urls.txt -o results.jsonl --rate 100/min --retries 2
  reqstorm urls.txt --estimate --rate 100/min
  cat urls.txt | reqstorm --rate auto > results.jsonl
  reqstorm users.csv --template "https://api.example.com/users/{id}" -o users.db
  reqstorm urls.txt --paginate next:links.next --schema products.json --explode items -o shop.db
  reqstorm urls.txt --infer-schema 5 --explode items > products.json

exit status: 0 when every request succeeded, 1 when some failed or records were rejected,
2 for invalid arguments.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reqstorm",
        description="Send many HTTP requests with rate limits, retries and progress, and save the results.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", nargs="?", default="-",
                        help="file with one URL per line, or a CSV with --template "
                        "(default: standard input)")  # fmt: skip
    parser.add_argument("-o", "--output", help="write results to a .jsonl, .csv or .db/.sqlite file "
                        "(default: JSON lines on standard output)")  # fmt: skip
    parser.add_argument("--version", action="version", version=f"reqstorm {__version__}")

    request = parser.add_argument_group("requests")
    request.add_argument("-X", "--method", default="GET")
    request.add_argument("-H", "--header", action="append", default=[], metavar="'NAME: VALUE'")
    request.add_argument("--json", help="JSON body sent with every request")
    request.add_argument("--data", help="raw body sent with every request")
    request.add_argument("--template", metavar="URL", help="URL template filled from each CSV row, "
                         "e.g. 'https://api.example.com/users/{id}'")  # fmt: skip
    request.add_argument("--bearer", metavar="TOKEN", help="send 'Authorization: Bearer TOKEN' "
                         "(or set REQSTORM_TOKEN)")  # fmt: skip
    request.add_argument(
        "--proxy",
        action="append",
        default=[],
        metavar="URL",
        help="http://, socks5://, socks5h:// or socks4:// proxy URL; repeat to rotate",
    )
    request.add_argument("--no-verify", action="store_true", help="do not verify TLS certificates")

    pace = parser.add_argument_group("pace and retries")
    pace.add_argument("--rate", help="per-host rate limit: 5, 10/s, 100/min, 1000/h, or auto")
    pace.add_argument("-c", "--concurrency", type=int, default=100)
    pace.add_argument("--per-host", type=int, default=0, metavar="N", help="max requests in flight per host")
    pace.add_argument("--timeout", type=float, default=30.0, help="seconds per attempt (default 30)")
    pace.add_argument("--retries", type=int, default=0)
    pace.add_argument(
        "--backoff", type=float, default=0.5, help="seconds before the first retry, doubled after"
    )
    pace.add_argument(
        "--max-backoff", type=float, default=30.0, help="longest wait between retries (default 30)"
    )
    pace.add_argument("--no-jitter", action="store_true", help="wait exactly, not a random part of the delay")
    pace.add_argument("--retry-rounds", type=int, default=0)
    pace.add_argument("--retry-round-delay", type=float, default=5.0)
    pace.add_argument("--paginate", metavar="STRATEGY",
                      help="next:PATH, link, cursor:PATH[:PARAM], or page[:PARAM[:ITEMS_PATH]]")  # fmt: skip
    pace.add_argument("--max-pages", type=int, default=1000)
    pace.add_argument(
        "--cache", metavar="FILE", help="reuse responses from this cache file (revalidated with ETag)"
    )
    pace.add_argument(
        "--cache-ttl", type=float, help="seconds a cached response is used without asking the server"
    )

    output = parser.add_argument_group("output")
    output.add_argument("--flush-interval", type=float, default=1.0, metavar="SECONDS",
                        help="write waiting results to --output at least this often (default 1)")  # fmt: skip
    output.add_argument(
        "--resume", action="store_true", help="skip requests already saved successfully in --output"
    )
    output.add_argument("--ordered", action="store_true", help="write results in input order")
    output.add_argument("--body", choices=["text", "base64", "none"], default="text")
    output.add_argument("--include-headers", action="store_true")
    output.add_argument("--schema", metavar="FILE", help="JSON file mapping columns to fields: "
                        '{"price": {"path": "pricing.amount", "type": "float"}}')  # fmt: skip
    output.add_argument("--explode", metavar="PATH", help="with --schema: one row per element of this array")
    output.add_argument("--table", default="reqstorm_results", help="table name for .db output")
    output.add_argument("--rejects-table", help="with --schema and .db output: table for rejected records")
    output.add_argument("--infer-schema", type=int, metavar="N",
                        help="fetch the first N inputs, print a draft --schema file and exit")  # fmt: skip

    info = parser.add_argument_group("information")
    info.add_argument("--estimate", action="store_true", help="print how long the batch would take and exit")
    info.add_argument("--latency", type=float, default=0.5, help="typical response time for --estimate")
    info.add_argument("-q", "--quiet", action="store_true", help="no progress line; log only errors")
    info.add_argument("-v", "--verbose", action="count", default=0,
                      help="log more: -v run events (INFO), -vv every attempt (DEBUG)")  # fmt: skip
    info.add_argument("--log-file", metavar="PATH",
                      help="write the log to a file instead of stderr; a directory or a name with {time} "
                      "gets a time-stamped file, and an existing file is never overwritten")  # fmt: skip
    info.add_argument("--log-json", action="store_true", help="log one JSON object per line")
    info.add_argument(
        "--report", action="store_true", help="print response times, statuses and hosts at the end"
    )
    return parser


def _paginator(text: Optional[str], max_pages: int) -> Optional[Paginator]:
    if not text:
        return None
    kind, _, rest = text.partition(":")
    args = [part for part in rest.split(":") if part] if rest else []
    if kind == "next":
        return NextLink(args[0] if args else "next", max_pages=max_pages)
    if kind == "link":
        return LinkHeader(max_pages=max_pages)
    if kind == "cursor" and args:
        return Cursor(args[0], param=args[1] if len(args) > 1 else "cursor", max_pages=max_pages)
    if kind == "page":
        return PageNumber(
            args[0] if args else "page", items=args[1] if len(args) > 1 else None, max_pages=max_pages
        )
    raise ValueError(
        f"invalid --paginate {text!r}; use next:PATH, link, cursor:PATH[:PARAM] or page[:PARAM[:ITEMS]]"
    )


def _schema_from_file(path: str, explode: Optional[str]) -> Schema:
    with open(path, encoding="utf-8") as file:
        definition = json.load(file)
    if isinstance(definition, dict) and "fields" in definition:
        explode = explode or definition.get("explode")
        definition = definition["fields"]
    if not isinstance(definition, dict):
        raise SchemaError("a schema file must be a JSON object of column name to field")
    fields: Dict[str, Field] = {}
    for column, spec in definition.items():
        if isinstance(spec, str):
            spec = {"path": spec}
        types = {"int": int, "float": float, "str": str, "bool": bool}
        kind = spec.get("type", "json")
        fields[column] = Field(
            spec.get("path", column),
            types.get(kind, kind),
            required=bool(spec.get("required", False)),
            coerce=bool(spec.get("coerce", False)),
            key=bool(spec.get("key", False)),
        )
    return Schema(fields, explode)


def _schema_to_json(schema: Schema) -> str:
    fields: Dict[str, Any] = {}
    for column, spec in schema.fields.items():
        entry: Dict[str, Any] = {"path": spec.path, "type": spec.type_name}
        for option in ("required", "coerce", "key"):
            if getattr(spec, option):
                entry[option] = True
        fields[column] = entry
    document: Dict[str, Any] = {"fields": fields}
    if schema.explode:
        document["explode"] = schema.explode
    return json.dumps(document, indent=2)


def _read_lines(path: str) -> Iterator[str]:
    stream = sys.stdin if path == "-" else open(path, encoding="utf-8")  # noqa: SIM115
    try:
        for line in stream:
            line = line.strip()
            if line and not line.startswith("#"):
                yield line
    finally:
        if stream is not sys.stdin:
            stream.close()


def _inputs(args: argparse.Namespace) -> Iterator[Union[str, Request]]:
    if args.template:
        if args.input == "-":
            raise ValueError("--template reads a CSV file; pass its path")
        yield from from_template(args.template, read_csv(args.input))
    else:
        yield from _read_lines(args.input)


def _count(args: argparse.Namespace) -> Optional[int]:
    if args.input == "-":
        return None
    if args.template:
        return sum(1 for _ in read_csv(args.input))
    return sum(1 for _ in _read_lines(args.input))


def _hosts(args: argparse.Namespace) -> int:
    from ._limits import host_key

    hosts = {host_key(item if isinstance(item, str) else item.url) for item in _inputs(args)}
    return max(len(hosts - {None}), 1)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (ValueError, KeyError, SchemaError, OSError, ImportError, json.JSONDecodeError) as error:
        message = error.args[0] if isinstance(error, KeyError) and error.args else error
        print(f"reqstorm: error: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("reqstorm: interrupted", file=sys.stderr)
        return 130


def _rate(text: Optional[str]) -> Any:
    if text is None or text == "auto":
        return text
    try:
        return float(text)
    except ValueError:
        parse_rate(text)  # raises ValueError with a clear message for a bad value
        return text


def _run(args: argparse.Namespace) -> int:
    cache = Cache(args.cache, ttl=args.cache_ttl) if args.cache and not args.estimate else None
    try:
        return _batch(args, cache)
    finally:
        if cache is not None:
            cache.close()


def _batch(args: argparse.Namespace, cache: Optional[Cache]) -> int:
    rate = _rate(args.rate)
    if args.estimate:
        total = _count(args)
        if total is None:
            raise ValueError("--estimate needs an input file, not standard input")
        print(estimate(total, rate_limit=None if rate == "auto" else rate, hosts=_hosts(args),
                       concurrency=args.concurrency, concurrency_per_host=args.per_host,
                       latency=args.latency))  # fmt: skip
        return 0

    headers: Dict[str, str] = {}
    for header in args.header:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            raise ValueError(f"invalid --header {header!r}; use 'Name: value'")
        headers[name.strip()] = value.strip()
    token = args.bearer or os.environ.get("REQSTORM_TOKEN")
    options: Dict[str, Any] = dict(
        method=args.method.upper(),
        headers=headers or None,
        json=json.loads(args.json) if args.json else None,
        data=args.data,
        concurrency=args.concurrency,
        concurrency_per_host=args.per_host,
        timeout=args.timeout,
        retries=args.retries,
        backoff=args.backoff,
        max_backoff=args.max_backoff,
        jitter=not args.no_jitter,
        verify_ssl=not args.no_verify,
        rate_limit=rate,
        auth=BearerAuth(token) if token else None,
        cache=cache,
        proxy=args.proxy or None,
        paginate=_paginator(args.paginate, args.max_pages),
    )
    schema = _schema_from_file(args.schema, args.explode) if args.schema else None

    if args.infer_schema:
        samples: List[Any] = []
        for index, item in enumerate(_inputs(args)):
            if index >= args.infer_schema:
                break
            samples.append(item)
        options.pop("paginate")
        drafted = infer_schema(fetch_all_sync(samples, **options), explode=args.explode)
        print(_schema_to_json(drafted))
        return 0

    progress: Any = False if args.quiet else True
    options["total"] = _count(args) if not args.paginate else None
    options["log_level"] = "ERROR" if args.quiet else ("WARNING", "INFO", "DEBUG")[min(args.verbose, 2)]
    options["log_file"] = args.log_file
    options["log_format"] = "json" if args.log_json else "text"
    if args.output:
        summary = fetch_to_file_sync(
            _inputs(args), args.output, body=args.body, include_headers=args.include_headers,
            resume=args.resume, ordered=args.ordered, progress=progress, retry_rounds=args.retry_rounds,
            retry_round_delay=args.retry_round_delay, table=args.table, schema=schema,
            rejects_table=args.rejects_table, flush_interval=args.flush_interval, **options,
        )  # fmt: skip
        message = f"reqstorm: {summary.ok} ok, {summary.failed} failed, {summary.skipped} skipped"
        if schema is not None:
            message += f", {summary.rows} rows, {summary.rejected} rejected"
        print(message + f" -> {args.output}", file=sys.stderr)
        if summary.log_file:
            print(f"reqstorm: log written to {summary.log_file}", file=sys.stderr)
        if args.report:
            print(json.dumps(summary.report, indent=2), file=sys.stderr)
        return 0 if summary.failed == 0 and summary.rejected == 0 else 1

    if schema is not None or args.resume or args.retry_rounds:
        raise ValueError("--schema, --resume and --retry-rounds need --output")
    from ._report import Report

    report = Report()
    failed = 0
    for result in stream_sync(_inputs(args), progress=progress, **options):
        report.add(result)
        failed += 0 if result.ok else 1
        sys.stdout.write(json.dumps(result.to_dict(body=args.body, include_headers=args.include_headers),
                                    ensure_ascii=False) + "\n")  # fmt: skip
        sys.stdout.flush()
    if args.report:
        print(json.dumps(report.as_dict(), indent=2), file=sys.stderr)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
