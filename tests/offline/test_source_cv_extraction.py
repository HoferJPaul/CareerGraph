"""File -> text: content-based type detection, size/empty/encrypted/scanned/malformed refusal, safe DOCX
handling (zip bombs, DTDs, hostile entry names) and filename sanitisation. No model involved."""
import io
import time
import zipfile

import pytest

from docfixtures import make_docx, make_pdf
from source_cv import extraction as ex
from source_cv.errors import (
    DocumentTooLongError, EmptyDocumentError, EncryptedDocumentError, ExtractionFailedError, FileTooLargeError,
    ScannedPdfError, UnsupportedFileTypeError,
)
from source_fixtures import EMAIL, NAME, PHONE, cv_lines


def _extract(data: bytes):
    return ex.extract_text(data, ex.detect_kind(data))


# ---- accepted files ---------------------------------------------------------------------------------------


def test_text_pdf_yields_its_text_and_hyperlink_targets() -> None:
    doc = _extract(make_pdf([cv_lines()], links=["https://example.org/portfolio-only-in-link"]))
    assert doc.kind == "pdf" and doc.pages == 1
    assert NAME in doc.text and EMAIL in doc.text and PHONE in doc.text
    assert "Links found in the document" in doc.text and "https://example.org/portfolio-only-in-link" in doc.text


def test_multi_page_pdf_keeps_page_order() -> None:
    lines = [f"Line {i} of the curriculum vitae with enough text" for i in range(1, 7)]
    text = _extract(make_pdf([lines[:3], lines[3:]])).text
    assert [text.index(line) for line in lines] == sorted(text.index(line) for line in lines)


def test_docx_reads_paragraphs_header_table_textbox_and_hyperlinks_once() -> None:
    data = make_docx(
        [
            "Experience section with several words to pass the minimum",
            ("link", "GitHub", "https://github.com/mira-tannenbaum"),
            ("link", "Email me", "mailto:mira.tannenbaum@example.org"),
            ("table", [["2019 - 2021", "Analyst at Northfield"], ["Skills", "Python"]]),
            ("textbox", ["Sidebar contact block", "Vienna, Austria"]),
        ],
        header_lines=[NAME, EMAIL],
    )
    text = _extract(data).text
    assert text.startswith(NAME)  # the header (usually the contact block) comes first
    assert "GitHub (https://github.com/mira-tannenbaum)" in text
    assert "Email me (mira.tannenbaum@example.org)" in text and "mailto:" not in text
    assert "2019 - 2021  |  Analyst at Northfield" in text
    assert text.count("Sidebar contact block") == 1  # mc:Choice and mc:Fallback are the same box


def test_extraction_normalises_control_and_zero_width_characters() -> None:
    zero_width = chr(0x200B) + chr(0xAD)
    text = _extract(make_docx([f"Some{zero_width} text with{chr(0xA0)}odd whitespace inside this line of text"])).text
    assert chr(0x200B) not in text and chr(0xAD) not in text and chr(0xA0) not in text
    assert "Some text with odd whitespace" in text
    # control characters cannot occur in well-formed XML, but PDF text can carry them
    assert ex.normalize_text("a" + chr(0) + chr(7) + "b\r\nc") == "ab\nc"


# ---- type detection is by content, not by name ------------------------------------------------------------------


def test_type_is_decided_from_bytes() -> None:
    assert ex.detect_kind(make_pdf([cv_lines()])) == "pdf"
    assert ex.detect_kind(make_docx(cv_lines())) == "docx"


@pytest.mark.parametrize(
    "data",
    [
        b"just some plain text renamed to cv.pdf " * 20,
        bytes([0x89]) + b"PNG\r\n\x1a\n" + b"\x00" * 64,
        bytes([0xD0, 0xCF, 0x11, 0xE0]) + b"\x00" * 64,  # legacy .doc / password-protected Office
        b"<html><body>cv</body></html>",
    ],
)
def test_other_content_is_unsupported(data: bytes) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        ex.detect_kind(data)


def test_other_zip_based_office_files_are_unsupported() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")  # a spreadsheet, not a Word document
    with pytest.raises(UnsupportedFileTypeError):
        ex.detect_kind(buffer.getvalue())


def test_legacy_doc_message_explains_what_to_upload() -> None:
    with pytest.raises(UnsupportedFileTypeError, match="DOCX"):
        ex.detect_kind(bytes([0xD0, 0xCF, 0x11, 0xE0]) + b"\x00" * 16)


# ---- refusals ---------------------------------------------------------------------------------------------------


def test_size_limit_is_enforced_at_exactly_the_limit() -> None:
    ex.check_size(b"x" * 1000, 1000)
    with pytest.raises(FileTooLargeError, match="MB|larger"):
        ex.check_size(b"x" * 1001, 1000)
    assert ex.MAX_UPLOAD_BYTES == 5 * 1024 * 1024


def test_empty_input_and_textless_documents_are_refused() -> None:
    with pytest.raises(EmptyDocumentError):
        ex.detect_kind(b"")
    with pytest.raises(EmptyDocumentError):
        _extract(make_docx(["   ", ""]))
    with pytest.raises(EmptyDocumentError):
        _extract(make_pdf([[""]]))


def test_image_only_pdf_is_reported_as_scanned() -> None:
    with pytest.raises(ScannedPdfError) as info:
        _extract(make_pdf([[""]], image_only=True))
    assert "scan" in info.value.message.lower() and "DOCX" in info.value.message


def test_encrypted_pdf_is_refused_before_any_text_is_read() -> None:
    with pytest.raises(EncryptedDocumentError):
        _extract(make_pdf([cv_lines()], fake_encryption=True))


def test_really_encrypted_pdf_is_refused() -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([cv_lines()]))))
    try:
        writer.encrypt("secret", algorithm="RC4-128")
    except Exception:  # pragma: no cover - no RC4/AES backend available in this environment
        pytest.skip("pypdf cannot encrypt without a crypto backend here")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(EncryptedDocumentError):
        _extract(buffer.getvalue())


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntruncated garbage with no xref or trailer",
        b"%PDF-1.7\n" + bytes(range(256)) * 8,
    ],
)
def test_malformed_pdf_is_an_extraction_failure_not_a_crash(data: bytes) -> None:
    with pytest.raises((ExtractionFailedError, EmptyDocumentError, ScannedPdfError)):
        _extract(data)


def test_malformed_docx_is_an_extraction_failure() -> None:
    with pytest.raises(ExtractionFailedError):
        ex.detect_kind(b"PK\x03\x04" + b"this is not really a zip archive at all" * 5)
    broken_xml = make_docx([], document_xml="<w:document><w:body><w:p>never closed")
    with pytest.raises(ExtractionFailedError):
        _extract(broken_xml)


def test_pdf_with_too_many_pages_is_refused() -> None:
    pages = [["x"] for _ in range(ex.MAX_PDF_PAGES + 1)]
    with pytest.raises(DocumentTooLongError):
        _extract(make_pdf(pages))


# ---- DOCX archive safety -------------------------------------------------------------------------------------------


def _docx_with(extra: dict[str, bytes] | None = None, document_xml: str | None = None) -> bytes:
    return make_docx(cv_lines(), extra_parts=extra, document_xml=document_xml)


def test_zip_bomb_is_refused_quickly_without_being_expanded() -> None:
    bomb = b"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>" + b"a" * 30_000_000
    started = time.monotonic()
    with pytest.raises(ExtractionFailedError):
        _extract(_docx_with(document_xml=bomb.decode()))
    assert time.monotonic() - started < 5


def test_high_compression_ratio_part_is_refused_even_when_under_the_size_cap() -> None:
    ratio_bomb = "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body>" + "a" * 8_000_000
    with pytest.raises(ExtractionFailedError):
        _extract(_docx_with(document_xml=ratio_bomb))


def test_entity_declarations_are_refused() -> None:
    xml = (
        '<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>"
    )
    with pytest.raises(ExtractionFailedError):
        _extract(_docx_with(document_xml=xml))


def test_archive_with_path_traversal_names_is_refused() -> None:
    with pytest.raises(ExtractionFailedError):
        _extract(_docx_with(extra={"../../outside.txt": b"x"}))
    with pytest.raises(ExtractionFailedError):
        _extract(_docx_with(extra={"/absolute/evil": b"x"}))


def test_archive_with_too_many_entries_is_refused() -> None:
    extra = {f"customXml/item{i}.xml": b"<a/>" for i in range(ex.MAX_ZIP_ENTRIES + 5)}
    with pytest.raises(ExtractionFailedError):
        _extract(_docx_with(extra=extra))


def test_nothing_is_ever_written_to_disk_during_extraction(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _extract(_docx_with(extra={"word/media/image1.png": b"\x89PNG"}))
    assert list(tmp_path.iterdir()) == []


# ---- filenames --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("cv.pdf", "cv.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\Desktop\\My CV (final).docx", "My CV (final).docx"),
        ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
        ("   ", "source-cv"),
        ("", "source-cv"),
        (None, "source-cv"),
        ("....", "source-cv"),
        ("cv<script>alert(1)</script>.pdf", "script_.pdf"),
        ("résumé Müller.pdf", "résumé Müller.pdf"),
    ],
)
def test_filenames_are_sanitised(raw, expected) -> None:
    assert ex.sanitize_filename(raw) == expected


def test_control_characters_and_length_are_bounded() -> None:
    name = ex.sanitize_filename("cv" + chr(0) + chr(7) + chr(0x202E) + ("a" * 400) + ".pdf")
    assert len(name) <= 100 and name.endswith(".pdf")
    assert all(ord(c) >= 32 for c in name) and "/" not in name and "\\" not in name
