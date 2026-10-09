# Command line

The `reqstorm` command runs a batch without any Python code. Install it with `brew install melihcolpan/tap/reqstorm`, `pipx install "reqstorm[socks]"` or `uv tool install "reqstorm[socks]"`, or run it with Docker (below). Installing the Python package also installs the command.

```console
$ reqstorm urls.txt -o results.jsonl --rate 100/min --retries 2
reqstorm: 6987 ok, 13 failed, 0 skipped -> results.jsonl
```

The input is a file with one URL per line (empty lines and lines starting with `#` are skipped), or standard input. Without `-o`, results are written to standard output as JSON lines, so they can be piped:

```console
$ cat urls.txt | reqstorm --rate auto -q | jq 'select(.ok | not) | .url'
```

`python -m reqstorm` works the same way.

## Common tasks

```console
# How long will it take?
$ reqstorm urls.txt --estimate --rate 100/min
7000 requests: about 1h 10m (limited by rate_limit)

# Write to SQLite, CSV or JSON lines; .db and .sqlite files become SQLite
$ reqstorm urls.txt -o results.db --table pages

# Continue an interrupted run
$ reqstorm urls.txt -o results.jsonl --resume

# One request per CSV row
$ reqstorm users.csv --template "https://api.example.com/users/{id}" -o users.jsonl

# Follow the pages of an API and write typed rows
$ reqstorm urls.txt --infer-schema 5 --explode items > products.json
$ reqstorm urls.txt --paginate next:links.next \
    --schema products.json --explode items -o shop.db --table products

# Response times and per-host figures at the end
$ reqstorm urls.txt -o results.jsonl --report
```

## Options

| Option | Meaning |
|---|---|
| `-o FILE` | Output file: `.jsonl`, `.csv`, `.db` or `.sqlite`. Default: JSON lines on stdout |
| `-X`, `-H 'Name: value'`, `--json`, `--data` | Method, headers and body for every request |
| `--template URL` | Treat the input as CSV and fill `{column}` placeholders from each row |
| `--bearer TOKEN` | Send `Authorization: Bearer TOKEN`; the `REQSTORM_TOKEN` environment variable works too, and keeps the token out of your shell history |
| `--proxy URL` | Send through a proxy (`http://`, `socks5://`, `socks5h://`, `socks4://`, `socks4a://`); repeat the option to rotate through several |
| `--rate` | Per-host rate limit: `5`, `10/s`, `100/min`, `1000/h`, or `auto` |
| `-c`, `--per-host` | Concurrency in total and per host |
| `--timeout`, `--retries`, `--backoff` | Per attempt timeout, retries and the first wait between them |
| `--max-backoff`, `--no-jitter` | Longest wait between retries (default 30 s); wait exactly instead of a random part of it |
| `--retry-rounds`, `--retry-round-delay` | Send failed requests again at the end (needs `-o`) |
| `--paginate` | `next:PATH`, `link`, `cursor:PATH[:PARAM]` or `page[:PARAM[:ITEMS_PATH]]` |
| `--cache FILE`, `--cache-ttl` | Reuse responses, revalidated with ETag |
| `--schema FILE`, `--explode PATH` | Write typed rows instead of raw responses (needs `-o`) |
| `--infer-schema N` | Fetch the first N inputs and print a draft schema file |
| `--resume`, `--ordered`, `--body`, `--include-headers`, `--table`, `--rejects-table` | As in `fetch_to_file` |
| `--estimate`, `--latency` | Print the expected duration and exit |
| `--report` | Print response times, statuses and per-host figures to stderr |
| `-v`, `-vv` | Log more: run events (`INFO`), then every attempt (`DEBUG`). Warnings and errors are always logged |
| `-q` | No progress line, errors only |
| `--log-file PATH`, `--log-json` | Log to a file (time-stamped, never overwritten) and/or as JSON lines; see [Progress and logging](observability.md#log-files) |
| `--flush-interval SECONDS` | Write waiting results to `-o` at least this often (default 1) |

## Schema files

A schema file is JSON: each column maps to a field with a `path`, a `type` (`int`, `float`, `str`, `bool`, `datetime` or `json`) and optionally `required`, `coerce` and `key`. A plain string is a path to a JSON column. `--infer-schema` writes one for you to edit:

```json
{
  "fields": {
    "id": {"path": "id", "type": "int", "required": true, "key": true},
    "price": {"path": "pricing.amount", "type": "float"},
    "tags": "tags"
  },
  "explode": "items"
}
```

## Exit status

| Status | Meaning |
|---|---|
| 0 | Every request succeeded (and, with a schema, no record was rejected) |
| 1 | Some requests failed or records were rejected; see the output |
| 2 | Invalid arguments or an unreadable input or schema file |
| 130 | Interrupted with Ctrl+C |

## Docker

The image `ghcr.io/melihcolpan/reqstorm` contains the command with SOCKS proxy support, for `linux/amd64` and `linux/arm64`. Tags: `latest`, a release such as `2.4.1`, or a minor version such as `2.4`.

```console
# Files: mount the current directory at /data, the working directory in the container
$ docker run --rm -v "$PWD:/data" ghcr.io/melihcolpan/reqstorm urls.txt -o results.jsonl --rate 100/min

# Pipes: read URLs from stdin, write results to stdout
$ cat urls.txt | docker run --rm -i ghcr.io/melihcolpan/reqstorm -q > results.jsonl

# A token, without putting it on the command line
$ docker run --rm -v "$PWD:/data" -e REQSTORM_TOKEN ghcr.io/melihcolpan/reqstorm urls.txt -o out.jsonl
```

The container runs as an unprivileged user (uid 1000). If your directory is not writable for that user, add `--user "$(id -u):$(id -g)"`.
