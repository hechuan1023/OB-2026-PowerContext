# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CSV boundary behavior beyond the published airport workload."""

import asyncio
import csv
import io
import json

import pytest

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, TextEvidence
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


@pytest.mark.parametrize(
    "content",
    [
        b"\xef\xbb\xbf",
        b"\nid,name\n1,ok\n",
        b"id,name\n1,ok\n2,extra,field\n",
        b"id,name\n1,ok\n \t,blank\n",
        b'id,name\n1,ok\n2,"closed"trailing\n',
        b'id,name\n1,ok\n2,"unfinished',
        b"id,name\n1,ok\n2,\xff\n",
        b"id,name\n1,ok\n1,duplicate\n",
        b"id,name\n1,ok\n   \n",
    ],
)
def test_invalid_later_records_reject_the_entire_document(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="document", primary_key="id", content=content))


@pytest.mark.parametrize("document_id", ["", " ", "\tdoc", "doc\n", "\u3000doc"])
def test_document_identity_rejects_surrounding_whitespace(document_id: str) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key="id", content=b"id\n"))


def test_unicode_records_preserve_header_order_empty_strings_and_key_spaces() -> None:
    value = CSVDocumentInput(
        document_id="文档:/%",
        primary_key=" id ",
        content='" id ",id,备注,empty\n key ,001,"机场\r\n第二行",\nkey ,002,false,\n'.encode(),
    )
    rows = parse_csv_document(value)
    assert rows[0].columns == (" id ", "id", "备注", "empty")
    assert rows[0].values == (" key ", "001", "机场\r\n第二行", "")
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(rows[0]))
    assert isinstance(source, CSVRowSource)
    assert source.materialization == SourceMaterialization.CAPTURED
    assert source.name == source.source_id == "csv:%E6%96%87%E6%A1%A3%3A%2F%25:%20key%20"
    assert asyncio.run(registry.read(source)) == rows[0]
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == '{" id ":" key ","id":"001","备注":"机场\\r\\n第二行","empty":""}'
    assert list(json.loads(evidence.content)) == list(rows[0].columns)
    projected_row = evidence.metadata["row"]
    assert isinstance(projected_row, dict)
    assert list(projected_row) == list(rows[0].columns)
    assert set(evidence.metadata) == {"document_id", "primary_key", "primary_key_value", "columns", "row"}
    assert csv_row_source_id(value.document_id, rows[0].primary_key_value) != csv_row_source_id(
        value.document_id, rows[1].primary_key_value
    )
    with pytest.raises(ValueError):
        parse_csv_document(value.model_copy(update={"primary_key": "id "}))


def test_single_column_quoted_empty_record_is_not_ignored() -> None:
    assert parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=b"id\r\n\r\n")) == ()
    with pytest.raises(ValueError):
        parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=b'id\n""\n'))


def test_identity_length_is_measured_after_percent_encoding() -> None:
    assert len(csv_row_source_id("d", "x" * 250)) == 256
    assert len(csv_row_source_id("d", "界" * 27 + "x" * 7)) == 256
    for key in ("x" * 251, "界" * 28):
        with pytest.raises(ValueError):
            csv_row_source_id("d", key)
        with pytest.raises(ValueError):
            parse_csv_document(CSVDocumentInput(document_id="d", primary_key="id", content=f"id\n{key}\n".encode()))
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")
    assert csv_row_source_id("a%3Ab", "c") != csv_row_source_id("a:b", "c")


@pytest.mark.parametrize(
    "record",
    [
        'id,note\r\n1,"a\r\nb"\r\n',
        'id,note\n1,a"b\n',
        'id,note\n1,"a""b"\n',
        "id,note\n1,\\value\n",
        'id,note\n1,"\n\n"\n',
        'id,note\n1,"a" \n',
    ],
)
def test_csv_dialect_matches_standard_library_strict_semantics(record: str) -> None:
    try:
        expected = list(csv.reader(io.StringIO(record, newline=""), strict=True))
    except csv.Error:
        with pytest.raises(ValueError):
            parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=record.encode()))
    else:
        rows = parse_csv_document(CSVDocumentInput(document_id="doc", primary_key="id", content=record.encode()))
        assert rows[0].columns == tuple(expected[0])
        assert rows[0].values == tuple(expected[1])


@pytest.mark.parametrize(
    "updates",
    [
        {"primary_key_value": "other"},
        {"values": ("1",)},
        {"columns": ("id", "id")},
        {"values": (1, "note")},
    ],
)
def test_direct_row_inputs_validate_field_integrity(updates: dict[str, object]) -> None:
    fields = {
        "document_id": "doc",
        "primary_key": "id",
        "primary_key_value": "1",
        "columns": ("id", "note"),
        "values": ("1", "note"),
    }
    with pytest.raises(ValueError):
        CSVRowInput.model_validate(fields | updates)
