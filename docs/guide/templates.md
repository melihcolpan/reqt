# Requests from data

Often the requests come from a spreadsheet or a table: one row per user, product or order. `from_template` turns rows into requests:

```python
rows = reqstorm.read_csv("users.csv")          # id,country
requests = reqstorm.from_template(
    "https://api.example.com/users/{id}",
    rows,
    params={"country": "{country}"},
)
summary = reqstorm.fetch_to_file_sync(requests, "users.jsonl")
```

- `{field}` placeholders in the URL are filled from each row and percent-encoded, so a value such as `a b/c` stays one path segment and cannot change the URL.
- `json` and `params` take a template dict: a string that is exactly `"{field}"` keeps the value's own type (a number stays a number), other strings are formatted. They also accept a function of the row.
- Rows are read lazily, so a file with millions of rows is fine.
- A row without a field the URL needs raises a `KeyError` that names the field and the row.

## From a database

`read_sql` reads the rows of a query from any DB-API connection (sqlite3, psycopg, PyMySQL, ...), in batches:

```python
import psycopg

connection = psycopg.connect("dbname=shop")
rows = reqstorm.read_sql(
    connection,
    "SELECT id, sku FROM products WHERE updated_at > %s",
    (last_run,),
)
requests = reqstorm.from_template(
    "https://api.example.com/stock/{sku}", rows
)
reqstorm.fetch_to_db_sync(requests, connection, table="stock")
```

## A POST per row

```python
requests = reqstorm.from_template(
    "https://api.example.com/orders",
    reqstorm.read_csv("orders.csv"),
    method="POST",
    json={"customer": "{customer_id}", "items": [{"sku": "{sku}", "quantity": "{quantity}"}]},
)
```

Values from a CSV file are strings; use a function to convert them:

```python
json=lambda row: {"sku": row["sku"], "quantity": int(row["quantity"])}
```
