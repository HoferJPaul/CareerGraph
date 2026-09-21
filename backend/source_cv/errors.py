"""Errors for the Source CV feature.

Same contract as llm/errors.py: every error carries a fixed, human-readable, NON-SENSITIVE message,
because the API returns `to_detail()` verbatim. CV text, contact details, filenames and storage paths
are never placed in an error.
"""
from typing import Optional


class SourceCvError(Exception):
    code = "source_cv_error"
    http_status = 400
    retryable = False
    default_message = "The source CV request failed."

    def __init__(self, message: Optional[str] = None):
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def headers(self) -> dict[str, str]:
        return {}

    def to_detail(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class UnsupportedFileTypeError(SourceCvError):
    code = "unsupported_file_type"
    http_status = 415
    default_message = "Only text-based PDF and DOCX files are supported."


class FileTooLargeError(SourceCvError):
    code = "file_too_large"
    http_status = 413
    default_message = "The file is larger than the upload limit."


class EmptyDocumentError(SourceCvError):
    code = "empty_document"
    http_status = 422
    default_message = "The file is empty or contains no text."


class ScannedPdfError(SourceCvError):
    code = "scanned_or_unreadable_pdf"
    http_status = 422
    default_message = (
        "This PDF has no selectable text (it looks like a scan or an image). "
        "Export a text-based PDF or upload a DOCX instead."
    )


class EncryptedDocumentError(SourceCvError):
    code = "encrypted_document"
    http_status = 422
    default_message = "Encrypted or password-protected files are not supported. Remove the protection and upload again."


class ExtractionFailedError(SourceCvError):
    code = "extraction_failed"
    http_status = 422
    default_message = "The text could not be read from this file. It may be damaged or malformed."


class DocumentTooLongError(SourceCvError):
    code = "document_too_long"
    http_status = 413
    default_message = "The document contains more text than can be sent to the language model."


class NoCvContentError(SourceCvError):
    code = "no_cv_content"
    http_status = 422
    default_message = "Nothing that looks like a CV (contact details, experience or education) was found in this document."


class ProfileRejectedError(SourceCvError):
    """The model's parse failed schema or grounding validation, so nothing was stored."""

    code = "llm_schema_rejected"
    http_status = 502
    retryable = True
    default_message = (
        "The language model's parse of this CV failed validation, so it was rejected and your stored "
        "source CV was left unchanged. Try again."
    )

    def __init__(self, violations: Optional[dict[str, int]] = None):
        super().__init__()
        self.violations = dict(violations or {})

    def to_detail(self) -> dict:
        detail = super().to_detail()
        if self.violations:
            detail["violations"] = self.violations  # codes and counts only -- never CV text
        return detail


class ProfileVersionConflictError(SourceCvError):
    code = "profile_version_conflict"
    http_status = 409
    default_message = "The source CV changed since you loaded it. Reload it and apply your changes again."


class ProfileNotFoundError(SourceCvError):
    code = "source_cv_not_found"
    http_status = 404
    default_message = "No source CV has been uploaded."


class InvalidProfileEditError(SourceCvError):
    code = "invalid_profile"
    http_status = 422
    default_message = "Some of the submitted values are not valid."


class StorageFailureError(SourceCvError):
    code = "storage_failure"
    http_status = 500
    default_message = "The source CV could not be saved or read on the server."
