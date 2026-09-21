"""Builders for synthetic PDF and DOCX test files -- no fixtures on disk, no third-party writer.

Everything produced here is fictional and generated in memory, so no real CV ever enters the repo.
`make_pdf` writes a minimal but valid text PDF (Helvetica, WinAnsi); `make_docx` writes a minimal but
valid WordprocessingML package, with optional header, table, hyperlink and text-box content.
"""
import io
import zipfile
from typing import Optional, Sequence
from xml.sax.saxutils import escape


# ---- PDF ----------------------------------------------------------------------------------------------


def _pdf_string(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(
    pages: Sequence[Sequence[str]],
    *,
    image_only: bool = False,
    links: Sequence[str] = (),
    fake_encryption: bool = False,
) -> bytes:
    """One page per entry of `pages`, one text line per string. `image_only` draws a picture and no text
    (what a scan looks like); `links` become URI link annotations on page 1."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog_id = add(b"")  # placeholders, filled once the page ids are known
    pages_id = add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    image_id = (
        add(
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length 1 >>\nstream\n" + bytes([0x7F]) + b"\nendstream"
        )
        if image_only
        else None
    )

    link_ids: list[int] = []
    for uri in links:
        link_ids.append(
            add(
                b"<< /Type /Annot /Subtype /Link /Rect [50 50 300 70] /Border [0 0 0] "
                b"/A << /S /URI /URI (" + _pdf_string(uri) + b") >> >>"
            )
        )

    page_ids: list[int] = []
    for index, lines in enumerate(pages):
        if image_only:
            stream = b"q 300 0 0 300 50 400 cm /Im0 Do Q"
        else:
            parts = [b"BT", b"/F1 11 Tf", b"14 TL", b"50 780 Td"]
            for line in lines:
                parts.append(b"(" + _pdf_string(line) + b") Tj T*")
            parts.append(b"ET")
            stream = b"\n".join(parts)
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        annots = b""
        if index == 0 and link_ids:
            annots = b" /Annots [" + b" ".join(f"{i} 0 R".encode() for i in link_ids) + b"]"
        xobjects = b" /XObject << /Im0 " + f"{image_id} 0 R".encode() + b" >>" if image_id else b""
        page_ids.append(
            add(
                b"<< /Type /Page /Parent " + f"{pages_id} 0 R".encode() + b" /MediaBox [0 0 612 842] "
                b"/Resources << /Font << /F1 " + f"{font_id} 0 R".encode() + b" >>" + xobjects + b" >> /Contents "
                + f"{content_id} 0 R".encode() + annots + b" >>"
            )
        )

    objects[catalog_id - 1] = b"<< /Type /Catalog /Pages " + f"{pages_id} 0 R".encode() + b" >>"
    kids = b" ".join(f"{i} 0 R".encode() for i in page_ids)
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    trailer = f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R".encode()
    if fake_encryption:
        trailer += b" /Encrypt << /Filter /Standard /V 1 /R 2 /Length 40 /P -4 /O (" + b"0" * 32 + b") /U (" + b"0" * 32 + b") >>"
        trailer += b" /ID [<00000000000000000000000000000000> <00000000000000000000000000000000>]"
    trailer += b" >>"
    out.write(b"trailer\n" + trailer + f"\nstartxref\n{xref_at}\n%%EOF\n".encode())
    return out.getvalue()


# ---- DOCX ---------------------------------------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(text: str) -> str:
    return f"<w:p>{_run(text)}</w:p>"


def _document_xml(items: Sequence, rel_ids: dict[str, str]) -> str:
    body: list[str] = []
    for item in items:
        if isinstance(item, str):
            body.append(_paragraph(item))
        elif item[0] == "link":  # ("link", shown text, url)
            _, shown, url = item
            body.append(f'<w:p><w:hyperlink r:id="{rel_ids[url]}">{_run(shown)}</w:hyperlink></w:p>')
        elif item[0] == "table":  # ("table", [[cell, cell], ...])
            rows = "".join(
                "<w:tr>" + "".join(f"<w:tc>{_paragraph(cell)}</w:tc>" for cell in row) + "</w:tr>" for row in item[1]
            )
            body.append(f"<w:tbl>{rows}</w:tbl>")
        elif item[0] == "textbox":  # ("textbox", [line, line])
            box = "<w:txbxContent>" + "".join(_paragraph(line) for line in item[1]) + "</w:txbxContent>"
            body.append(
                f"<w:p><w:r><mc:AlternateContent><mc:Choice Requires=\"wps\">{box}</mc:Choice>"
                f"<mc:Fallback>{box}</mc:Fallback></mc:AlternateContent></w:r></w:p>"
            )
        else:
            raise ValueError(item)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}" xmlns:r="{_R_NS}" xmlns:mc="{_MC_NS}"><w:body>{"".join(body)}</w:body></w:document>'
    )


def make_docx(
    items: Sequence,
    *,
    header_lines: Sequence[str] = (),
    extra_parts: Optional[dict[str, bytes]] = None,
    document_xml: Optional[str] = None,
) -> bytes:
    """`items`: a string is a paragraph; ("link", text, url), ("table", rows) and ("textbox", lines)
    are the richer forms. `document_xml` replaces the generated body (used to plant hostile XML)."""
    urls = [item[2] for item in items if not isinstance(item, str) and item[0] == "link"]
    rel_ids = {url: f"rId{100 + i}" for i, url in enumerate(dict.fromkeys(urls))}
    rels = "".join(
        f'<Relationship Id="{rid}" Type="{_R_NS}/hyperlink" Target="{escape(url)}" TargetMode="External"/>'
        for url, rid in rel_ids.items()
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_R_NS}/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", document_xml or _document_xml(items, rel_ids))
        if rels:
            archive.writestr(
                "word/_rels/document.xml.rels",
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
            )
        if header_lines:
            paragraphs = "".join(_paragraph(line) for line in header_lines)
            archive.writestr(
                "word/header1.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><w:hdr xmlns:w="{_W_NS}">{paragraphs}</w:hdr>',
            )
        for name, data in (extra_parts or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()
