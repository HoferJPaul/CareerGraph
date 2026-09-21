"""Uploaded file -> plain text, with every safety check that has to happen before any model is involved.

  * The file type is decided by CONTENT (magic bytes, and for DOCX the required package parts) -- the
    filename and extension are never trusted. Only text-based PDF and DOCX are accepted.
  * Oversized, empty, encrypted, scanned/image-only and malformed files are refused with a specific error.
  * DOCX is read with the standard library only (no data is ever extracted to disk, so there is no path to
    traverse): the archive's entry count, declared sizes, compression ratio and entry names are bounded,
    reads are size-capped regardless of what the archive header claims, and XML carrying a DTD/entity
    declaration is refused (a CV never needs one; it is how "billion laughs" style expansion works).
  * Nothing here logs or raises with document text: errors are fixed messages (source_cv/errors.py).
"""
import io
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Literal, Optional

from source_cv.errors import (
    DocumentTooLongError,
    EmptyDocumentError,
    EncryptedDocumentError,
    ExtractionFailedError,
    FileTooLargeError,
    ScannedPdfError,
    SourceCvError,
    UnsupportedFileTypeError,
)

FileKind = Literal["pdf", "docx"]

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 15
MAX_EXTRACTED_CHARS = 250_000  # hard ceiling; the (configurable) model-input limit is applied later
MIN_TEXT_CHARS = 40  # fewer letters/digits than this is "no text", whatever the file claims

MAX_ZIP_ENTRIES = 500
MAX_ZIP_TOTAL_UNCOMPRESSED = 60 * 1024 * 1024
MAX_XML_PART_BYTES = 12 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200

logging.getLogger("pypdf").setLevel(logging.ERROR)  # pypdf's warnings describe file structure; keep them off our logs

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_HYPERLINK_FIELD = re.compile(r'HYPERLINK\s+"([^"]+)"', re.IGNORECASE)
_SAFE_LINK = re.compile(r"^(?:https?://|mailto:)", re.IGNORECASE)

_CONTROL_CHARS = "".join(chr(c) for c in [*range(0, 9), 11, 12, *range(14, 32), 127, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0xAD])
_CONTROL_TABLE = {ord(c): None for c in _CONTROL_CHARS}
_NBSP = chr(0xA0)


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    kind: FileKind
    pages: Optional[int] = None


# ---- filenames ----------------------------------------------------------------------------------------


def sanitize_filename(name: Optional[str]) -> str:
    """A display-only filename: no directory parts, no control characters, no odd punctuation, bounded
    length. It is stored as metadata and shown back to the user; it is NEVER used to build a path (the
    stored upload gets a server-generated name)."""
    if not name:
        return "source-cv"
    base = re.split(r"[\\/]", name)[-1]
    base = unicodedata.normalize("NFKC", base).translate(_CONTROL_TABLE)
    base = re.sub(r"[^\w .()\-]", "_", base)
    base = re.sub(r"\.{2,}", ".", base)
    base = re.sub(r"[ _]{2,}", " ", base).strip(" ._-")
    if not base:
        return "source-cv"
    if len(base) > 100:
        stem, dot, ext = base.rpartition(".")
        if dot and 1 <= len(ext) <= 5:
            base = stem[: 100 - len(ext) - 1] + "." + ext
        else:
            base = base[:100]
    return base


# ---- type detection -----------------------------------------------------------------------------------


def detect_kind(data: bytes) -> FileKind:
    """PDF or DOCX, decided from the bytes. Everything else -- including legacy .doc, other zip-based
    Office files, images, text renamed to .pdf -- is unsupported."""
    if not data:
        raise EmptyDocumentError()
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
            raise ExtractionFailedError() from None
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return "docx"
        raise UnsupportedFileTypeError()
    if data[:4] == bytes([0xD0, 0xCF, 0x11, 0xE0]):
        raise UnsupportedFileTypeError(
            "Legacy .doc and password-protected Office files are not supported. Save the CV as DOCX or a text-based PDF and upload that."
        )
    raise UnsupportedFileTypeError()


def check_size(data: bytes, max_bytes: int = MAX_UPLOAD_BYTES) -> None:
    if len(data) > max_bytes:
        raise FileTooLargeError(f"The file is larger than the {max_bytes // (1024 * 1024)} MB upload limit.")


# ---- text normalisation -------------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_CONTROL_TABLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(_NBSP, " ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _alnum_count(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


# ---- PDF ----------------------------------------------------------------------------------------------


def _page_has_image(page) -> bool:
    try:
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else None
        xobjects = resources.get("/XObject") if resources else None
        xobjects = xobjects.get_object() if xobjects is not None else None
        if not xobjects:
            return False
        return any(
            xobjects[name].get_object().get("/Subtype") == "/Image" for name in xobjects
        )
    except Exception:  # noqa: BLE001 - a structure we cannot read is simply "no image found"
        return False


def _page_links(page) -> list[str]:
    links: list[str] = []
    try:
        annotations = page.get("/Annots")
        annotations = annotations.get_object() if annotations is not None else []
        for annotation in annotations:
            action = annotation.get_object().get("/A")
            uri = action.get_object().get("/URI") if action is not None else None
            if isinstance(uri, str) and _SAFE_LINK.match(uri.strip()):
                links.append(uri.strip())
    except Exception:  # noqa: BLE001 - links are a best-effort extra
        pass
    return links


def _extract_pdf(data: bytes) -> ExtractedDocument:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise EncryptedDocumentError()
        page_count = len(reader.pages)
        if page_count == 0:
            raise EmptyDocumentError()
        if page_count > MAX_PDF_PAGES:
            raise DocumentTooLongError(f"The PDF has more than {MAX_PDF_PAGES} pages. Upload the CV itself, not a longer document.")

        chunks: list[str] = []
        links: list[str] = []
        any_image = False
        readable_pages = 0
        total = 0
        for page in reader.pages:
            try:
                page_text = page.extract_text() or ""
                readable_pages += 1
            except Exception:  # noqa: BLE001 - one unreadable page must not sink the others
                page_text = ""
            total += len(page_text)
            if total > MAX_EXTRACTED_CHARS:
                raise DocumentTooLongError()
            chunks.append(page_text)
            links.extend(_page_links(page))
            any_image = any_image or _page_has_image(page)
    except SourceCvError:
        raise
    except Exception:  # noqa: BLE001 - pypdf raises many unrelated types for damaged files
        raise ExtractionFailedError() from None

    if readable_pages == 0:
        raise ExtractionFailedError()
    text = normalize_text("\n".join(chunks))
    if _alnum_count(text) < MIN_TEXT_CHARS:
        if any_image or text:
            raise ScannedPdfError()
        raise EmptyDocumentError()
    unique_links = [link for link in dict.fromkeys(links) if link not in text]
    if unique_links:
        text += "\n\nLinks found in the document:\n" + "\n".join(unique_links)
    return ExtractedDocument(text=text, kind="pdf", pages=page_count)


# ---- DOCX ---------------------------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _read_part(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_XML_PART_BYTES:
        raise ExtractionFailedError()
    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        raise ExtractionFailedError()
    with archive.open(info) as handle:
        data = handle.read(MAX_XML_PART_BYTES + 1)  # capped regardless of what the header declared
    if len(data) > MAX_XML_PART_BYTES:
        raise ExtractionFailedError()
    return data


def _parse_xml(data: bytes) -> ET.Element:
    # Word writes UTF-8. A DTD or entity declaration has no place in a document part and is exactly how
    # entity-expansion attacks are built, so any part carrying one is refused outright.
    if data[:2] in (bytes([0xFF, 0xFE]), bytes([0xFE, 0xFF])) or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ExtractionFailedError()
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise ExtractionFailedError() from None


def _relationships(archive: zipfile.ZipFile, part_name: str) -> dict[str, str]:
    directory, _, file_name = part_name.rpartition("/")
    rels_name = f"{directory}/_rels/{file_name}.rels"
    try:
        info = archive.getinfo(rels_name)
    except KeyError:
        return {}
    root = _parse_xml(_read_part(archive, info))
    return {
        rel.get("Id", ""): rel.get("Target", "")
        for rel in root
        if rel.get("TargetMode") == "External" and _SAFE_LINK.match(rel.get("Target", ""))
    }


def _display_link(target: str) -> str:
    return target[len("mailto:"):] if target.lower().startswith("mailto:") else target


class _DocxReader:
    """Turns a WordprocessingML tree into lines of text in reading order: paragraphs, table rows (cells
    of a one-line row joined with a separator), text boxes, and hyperlink targets."""

    def __init__(self, rels: dict[str, str]):
        self.rels = rels
        self.lines: list[str] = []

    def block(self, container: ET.Element) -> None:
        for child in container:
            tag = _local(child.tag)
            if tag == "p":
                self.paragraph(child)
            elif tag == "tbl":
                self.table(child)
            elif tag == "AlternateContent":
                for choice in child:
                    if _local(choice.tag) != "Fallback":  # Choice and Fallback repeat the same content
                        self.block(choice)
            elif tag in ("sdt", "sdtContent", "body", "hdr", "ftr", "Choice", "ins", "customXml", "smartTag"):
                self.block(child)

    def table(self, table: ET.Element) -> None:
        for row in table.iter(f"{_W}tr"):
            cells: list[list[str]] = []
            for cell in row.findall(f"{_W}tc"):
                sub = _DocxReader(self.rels)
                sub.block(cell)
                cells.append([line for line in sub.lines if line.strip()])
            if cells and all(len(lines) <= 1 for lines in cells):
                joined = "  |  ".join(lines[0] for lines in cells if lines)
                if joined:
                    self.lines.append(joined)
            else:
                for lines in cells:
                    self.lines.extend(lines)

    def paragraph(self, paragraph: ET.Element) -> None:
        pieces: list[str] = []
        nested: list[ET.Element] = []
        for child in paragraph:
            self._collect(child, pieces, nested)
        text = "".join(pieces)
        self.lines.extend(text.split("\n") if text else [""])
        for box in nested:
            self.block(box)

    def _collect(self, element: ET.Element, out: list[str], nested: list[ET.Element]) -> None:
        tag = _local(element.tag)
        if tag == "t":
            out.append(element.text or "")
        elif tag == "tab":
            out.append("\t")
        elif tag in ("br", "cr"):
            out.append("\n")
        elif tag == "noBreakHyphen":
            out.append("-")
        elif tag == "txbxContent":
            nested.append(element)
        elif tag in ("Fallback", "delText", "del"):
            return
        elif tag == "instrText":
            match = _HYPERLINK_FIELD.search(element.text or "")
            if match and _SAFE_LINK.match(match.group(1)):
                out.append(f" ({_display_link(match.group(1))})")
        elif tag == "fldSimple":
            match = _HYPERLINK_FIELD.search(element.get(f"{_W}instr", ""))
            for child in element:
                self._collect(child, out, nested)
            if match and _SAFE_LINK.match(match.group(1)):
                out.append(f" ({_display_link(match.group(1))})")
        elif tag == "hyperlink":
            start = len(out)
            for child in element:
                self._collect(child, out, nested)
            target = self.rels.get(element.get(f"{_R}id", ""))
            shown = "".join(out[start:])
            if target and _display_link(target) not in shown:
                out.append(f" ({_display_link(target)})")
        else:
            for child in element:
                self._collect(child, out, nested)


def _extract_docx(data: bytes) -> ExtractedDocument:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        raise ExtractionFailedError() from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_TOTAL_UNCOMPRESSED:
            raise ExtractionFailedError()
        for info in infos:
            name = info.filename
            if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                raise ExtractionFailedError()  # nothing is extracted to disk, but such names mean a hostile archive
            if info.flag_bits & 0x1:
                raise EncryptedDocumentError()

        # Headers and footers usually hold the contact block, so they are read first.
        part_names = sorted(
            i.filename for i in infos if re.fullmatch(r"word/(?:header|footer)\d*\.xml", i.filename)
        ) + ["word/document.xml"]
        collected: list[str] = []
        try:
            for part_name in part_names:
                info = archive.getinfo(part_name)
                root = _parse_xml(_read_part(archive, info))
                reader = _DocxReader(_relationships(archive, part_name))
                reader.block(root)
                collected.extend(reader.lines)
                if sum(len(line) for line in collected) > MAX_EXTRACTED_CHARS:
                    raise DocumentTooLongError()
        except SourceCvError:
            raise
        except (KeyError, zipfile.BadZipFile, OSError, ValueError, RecursionError):
            raise ExtractionFailedError() from None

    text = normalize_text("\n".join(collected))
    if _alnum_count(text) < MIN_TEXT_CHARS:
        raise EmptyDocumentError()
    return ExtractedDocument(text=text, kind="docx")


# ---- entry point --------------------------------------------------------------------------------------


def extract_text(data: bytes, kind: FileKind) -> ExtractedDocument:
    return _extract_pdf(data) if kind == "pdf" else _extract_docx(data)
