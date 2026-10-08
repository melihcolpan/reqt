# Pagination

Many APIs return a list one page at a time. Pass `paginate=` and reqstorm follows each starting URL through all of its pages, while other starting URLs and their pages run concurrently under the same rate limits.

```python
results = reqstorm.fetch_all_sync(
    ["https://api.example.com/products"],
    paginate=reqstorm.NextLink("links.next"),
    rate_limit="auto",
)
```

Each result carries `page` (0 for the starting URL, 1 for the next page, ...) and `seed_index` (the position of its starting URL in your input). `index` stays unique, in the order the pages were requested.

## Strategies

| Strategy | The next page is ... | Example API response |
|---|---|---|
| `NextLink("links.next")` | a URL in the JSON body (relative URLs are resolved) | `{"items": [...], "links": {"next": "/products?page=2"}}` |
| `LinkHeader()` | in the `Link` response header (RFC 8288), as GitHub does | `Link: <https://api.github.com/...&page=2>; rel="next"` |
| `Cursor("meta.next_cursor", param="cursor")` | requested with a cursor from the body as a query parameter | `{"data": [...], "meta": {"next_cursor": "eyJpZCI6NDJ9"}}` |
| `PageNumber("page", items="data")` | `?page=2`, `?page=3`, ... until the `items` array is empty | `{"data": []}` ends it |

Pagination stops when there is no next page, when a page fails (after its retries), when the next page is the same as the current one, or after `max_pages` pages per starting URL (1000 by default):

```python
reqstorm.Cursor("next", param="after", max_pages=50)
```

Paths use the same syntax as [schema paths](structured-data.md#paths-and-nested-json): dotted keys and list indexes, such as `"paging.cursors.after"` or `"links[0].href"`.

## Straight into a table

Pagination combines with schemas and `explode`, so a whole catalogue becomes typed rows in one call. Key fields make a second run update rows instead of adding them:

```python
schema = {
    "id": reqstorm.Field("id", int, key=True),
    "name": reqstorm.Field("name", str),
    "price": reqstorm.Field("pricing.amount", float),
}
summary = reqstorm.fetch_to_file_sync(
    ["https://api.example.com/products"],
    "shop.db",
    table="products",
    schema=schema,
    explode="items",
    paginate=reqstorm.NextLink("links.next"),
)
```

All rows from one starting URL share its `source_index` and `source_url`.

## Limitations

- `retry_rounds` cannot be combined with `paginate`: a page retried at the end would not continue the pagination. Use `retries` instead; pages are retried as soon as they fail.
- `resume` cannot be combined with `paginate`: which pages come after the last one written is only known by fetching again. Use key fields with a schema, so running again updates rows in place.
- Your own strategy is a subclass of `reqstorm.Paginator` with a `next(request, result, page)` method that returns the next `reqstorm.Request` or `None`.
