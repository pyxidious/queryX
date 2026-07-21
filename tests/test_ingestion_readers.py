from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from queryx.app.ingestion.readers.csv import CSVReader
from queryx.app.ingestion.readers.parquet import ParquetReader


def test_csv_inspection_is_bounded_and_infers_header_types(tmp_path: Path) -> None:
    path = tmp_path / "people.csv"
    path.write_text("id,name,active,score\n1,Ada,true,9.5\n2,,false,10\n3,Linus,true,8.25\n", encoding="utf-8")

    result = CSVReader(count_limit=2).inspect(path, preview_limit=1, sample_limit=2)
    fields = {field.name: field for field in result.fields}

    assert result.metadata["has_header"] is True
    assert result.records_detected == 2
    assert result.records_estimated is True
    assert len(result.preview) == 1
    assert fields["id"].data_type == "integer"
    assert fields["active"].data_type == "boolean"
    assert fields["score"].data_type == "number"
    assert fields["name"].nullable is True


def test_csv_sample_nullability_is_conservative_and_empty_fields_are_null(tmp_path: Path) -> None:
    path = tmp_path / "orders.csv"
    path.write_text(
        "order_id,approved_at,delivered_at\n"
        "a1,2018-01-01 10:00:00,2018-01-03 12:00:00\n"
        "a2,2018-01-02 11:00:00,\n"
        "a3,,\n",
        encoding="utf-8",
    )

    result = CSVReader().inspect(path, preview_limit=3, sample_limit=1)

    assert [field.data_type for field in result.fields] == ["string", "datetime", "datetime"]
    assert all(field.nullable for field in result.fields)
    assert result.metadata["sampled_rows"] == 1
    assert result.metadata["nullability_basis"] == "sampled_conservative"
    preview = CSVReader().preview(path, 3)
    assert preview[1]["delivered_at"] is None
    assert preview[2]["approved_at"] is None


def test_parquet_inspection_reads_footer_schema_and_limited_preview(tmp_path: Path) -> None:
    path = tmp_path / "people.parquet"
    pq.write_table(pa.table({"id": [1, 2, 3], "name": ["Ada", "Grace", "Linus"]}), path)

    result = ParquetReader().inspect(path, preview_limit=2, sample_limit=10)

    assert result.records_detected == 3
    assert result.records_estimated is False
    assert result.metadata["row_groups"] == 1
    assert [field.name for field in result.fields] == ["id", "name"]
    assert len(result.preview) == 2


def test_parquet_declared_nullability_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "declared.parquet"
    schema = pa.schema(
        [pa.field("required_id", pa.int64(), nullable=False), pa.field("optional_name", pa.string())]
    )
    pq.write_table(pa.Table.from_arrays([pa.array([1, 2]), pa.array(["Ada", None])], schema=schema), path)

    result = ParquetReader().inspect(path, preview_limit=1, sample_limit=1)

    assert [(field.name, field.nullable) for field in result.fields] == [
        ("required_id", False),
        ("optional_name", True),
    ]
