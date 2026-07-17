from __future__ import annotations

from queryx.app.connectors.mongodb import infer_mongo_schema


def test_infer_mongo_schema_supports_nested_objects_and_arrays() -> None:
    fields = infer_mongo_schema(
        [
            {
                "name": "Ada",
                "profile": {"age": 36, "active": True},
                "tags": ["vip", "analytics"],
                "events": [{"type": "login", "score": 1.5}],
            },
            {"name": "Grace", "profile": {"active": False}, "tags": []},
        ]
    )
    by_path = {field["path"]: field for field in fields}

    assert by_path["profile"]["types"] == ["object"]
    assert by_path["profile.age"]["types"] == ["int"]
    assert by_path["profile.age"]["presence"] == 0.5
    assert by_path["tags"]["types"] == ["array"]
    assert by_path["tags[]"]["types"] == ["str"]
    assert by_path["events[]"]["types"] == ["object"]
    assert by_path["events[].score"]["types"] == ["float"]
