from hashlib import sha256
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from semantic_pipeline_v2.legacy_fields import _caption_positions, recover_pdf_fields
from semantic_pipeline_v2.originals import Original


def test_field_recovery_reads_only_retained_pages_of_verified_original(tmp_path):
    writer = PdfWriter()
    for amount in ("12.00", "48.00"):
        page = writer.add_blank_page(300, 300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 30 200 Td (Balance     SGD {amount}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "balances.pdf"
    writer.write(path)
    digest = sha256(path.read_bytes()).hexdigest()
    original = Original(tmp_path, Path(path.name), digest, path.stat().st_size)
    old = {"sourceSha256": digest, "observations": [
        {"quote": "[tbl-0.md](tbl-0.md)", "citation": {"kind": "ocr_markdown", "page": 2}},
    ]}
    recovered = recover_pdf_fields(old, original)
    assert recovered["fieldOrigin"] == "pinned_pdf_text_layer"
    field, = recovered["fields"]
    assert (field["value"], field["source"]["page"]) == ("SGD 48.00", 2)
    assert field["source"]["kind"] == "pdf_text_layer"
    assert "fields" not in old
    new = {**old, "fields": [], "fieldStatus": "available"}
    assert recover_pdf_fields(new, original) is new
    path.write_bytes(b"changed")
    assert recover_pdf_fields(old, original) is old


def test_historical_table_heading_requires_unique_full_table_and_body_heading():
    table = {"id": "arbitrary.md", "rows": [["Account type", "Current"], ["Status", "Open"]]}
    layout = "Account information\n Account type     Current\n Status      Open\n"
    body = [{"quote": "## Account information", "citation": {"kind": "ocr_markdown", "startLine": 7}}]
    assert _caption_positions(layout, [table], body) == {
        "arbitrary.md": {"caption": "Account information", "afterBodyLine": 7},
    }
    assert _caption_positions(layout + layout, [table], body) == {}
    assert _caption_positions(layout, [table], body * 2) == {}
    assert _caption_positions(layout.replace("Open", "Closed"), [table], body) == {}
