import sqlite3

import pytest

import reqstorm


def test_values_are_filled_and_percent_encoded():
    rows = [{"id": 7, "name": "a b/c"}]
    (request,) = reqstorm.from_template("https://api.example.com/users/{id}/{name}", rows)
    assert request.url == "https://api.example.com/users/7/a%20b%2Fc"


def test_json_and_params_templates_keep_value_types():
    rows = [{"id": "7", "qty": 3, "tags": ["x"]}]
    (request,) = reqstorm.from_template(
        "https://api.example.com/orders", rows, method="POST",
        json={"item": "{id}", "quantity": "{qty}", "tags": "{tags}", "note": "order {id}"},
        params=lambda row: {"user": row["id"]},
    )  # fmt: skip
    assert request.method == "POST"
    assert request.json == {"item": "7", "quantity": 3, "tags": ["x"], "note": "order 7"}
    assert request.params == {"user": "7"}


def test_missing_field_names_the_row():
    with pytest.raises(KeyError, match="'id'"):
        list(reqstorm.from_template("https://x/{id}", [{"other": 1}]))


def test_rows_are_read_lazily():
    def rows():
        yield {"id": 1}
        raise AssertionError("read too far")

    requests = reqstorm.from_template("https://x/{id}", rows())
    assert next(requests).url == "https://x/1"


def test_read_csv(tmp_path):
    path = tmp_path / "users.csv"
    path.write_text("﻿id;name\n1;Ayşe\n2;Mehmet\n", encoding="utf-8")
    assert list(reqstorm.read_csv(path, delimiter=";")) == [
        {"id": "1", "name": "Ayşe"},
        {"id": "2", "name": "Mehmet"},
    ]


def test_read_sql_in_batches():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    connection.executemany("INSERT INTO users VALUES (?, ?)", [(n, f"u{n}") for n in range(5)])
    rows = list(reqstorm.read_sql(connection, "SELECT id, name FROM users WHERE id > ?", (1,), batch_size=2))
    connection.close()
    assert rows == [{"id": 2, "name": "u2"}, {"id": 3, "name": "u3"}, {"id": 4, "name": "u4"}]


async def test_template_requests_end_to_end(server, tmp_path):
    csv_path = tmp_path / "ids.csv"
    csv_path.write_text("key\nalpha\nbeta gamma\n")
    requests = reqstorm.from_template(server + "/echo?key={key}", reqstorm.read_csv(csv_path))
    results = await reqstorm.fetch_all(requests)
    assert [r.json()["query"]["key"] for r in results] == ["alpha", "beta gamma"]
