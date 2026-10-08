import json
import sqlite3
import sys
from datetime import datetime
from typing import List, Optional

import pytest

pydantic = pytest.importorskip("pydantic")
from pydantic import BaseModel  # noqa: E402
from pydantic import Field as ModelField  # noqa: E402

import reqstorm  # noqa: E402


class Product(BaseModel):
    id: int = ModelField(json_schema_extra={"key": True})
    name: str
    price: Optional[float] = ModelField(None, json_schema_extra={"path": "pricing.amount"})
    tags: List[str] = []
    seen: Optional[datetime] = None
    page: Optional[int] = ModelField(None, json_schema_extra={"path": "$.meta.page"})


DOCUMENT = {
    "meta": {"page": 2},
    "items": [
        {
            "id": 1,
            "name": "Kettle",
            "pricing": {"amount": "12.5"},
            "tags": ["home"],
            "seen": "2026-10-01T10:00:00Z",
        },
        {"id": "not a number", "name": "Broken"},
        {"id": 3, "name": "Lamp", "pricing": {"amount": float("nan")}},
    ],
}


def test_model_fields_become_columns():
    schema = (
        reqstorm.Schema.from_model(Product, explode="items")
        if hasattr(reqstorm.Schema, "from_model")
        else None
    )
    from reqstorm._schema import as_schema

    schema = as_schema(Product, "items")
    assert {c: (f.path, f.type_name, f.required, f.key) for c, f in schema.fields.items()} == {
        "id": ("id", "int", True, True),
        "name": ("name", "str", True, False),
        "price": ("pricing.amount", "float", False, False),
        "tags": ("tags", "json", False, False),
        "seen": ("seen", "datetime", False, False),
        "page": ("$.meta.page", "int", False, False),
    }


def test_validation_and_rejects_follow_the_model():
    from reqstorm._schema import as_schema

    rows, rejected = as_schema(Product, "items").rows(DOCUMENT)
    assert rows == [{"id": 1, "name": "Kettle", "price": 12.5, "tags": ["home"],
                     "seen": datetime.fromisoformat("2026-10-01T10:00:00+00:00"), "page": 2}]  # fmt: skip
    reasons = [entry["reasons"][0] for entry in rejected]
    assert reasons[0].startswith("id: Input should be a valid integer")
    assert reasons[1] == "price: NaN or Infinity is not allowed"


async def test_model_as_schema_to_sqlite(server, tmp_path):
    body = json.dumps({**DOCUMENT, "items": DOCUMENT["items"][:2]})
    target = tmp_path / "products.db"
    summary = await reqstorm.fetch_to_file(
        [reqstorm.Request(server + "/json", params={"body": body})], target, table="products",
        schema=Product, explode="items", rejects_table="rejected",
    )  # fmt: skip
    assert summary.rows == 1 and summary.rejected == 1
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT id, name, price, tags, page FROM products").fetchall() == [
            (1, "Kettle", 12.5, '["home"]', 2)
        ]
        assert connection.execute("SELECT count(*) FROM rejected").fetchone() == (1,)


async def test_extract_with_a_model(server):
    results = await reqstorm.fetch_all(
        [
            reqstorm.Request(
                server + "/json", params={"body": json.dumps({**DOCUMENT, "items": DOCUMENT["items"][:2]})}
            )
        ]
    )
    rows, rejected = reqstorm.extract(results, Product, explode="items")
    assert [row["id"] for row in rows] == [1] and len(rejected) == 1


def test_pydantic_v1_style_objects_are_not_models():
    from reqstorm._pydantic import is_model

    assert not is_model(dict) and not is_model(Product(id=1, name="x"))


@pytest.mark.skipif(sys.version_info < (3, 10), reason="X | None needs Python 3.10")
def test_union_syntax_is_understood():
    from reqstorm._schema import as_schema

    namespace = {}
    exec(
        "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int | None = None\n    b: int | str = 1",
        namespace,
    )
    fields = as_schema(namespace["M"]).fields
    assert fields["a"].type_name == "int" and fields["b"].type_name == "json"
