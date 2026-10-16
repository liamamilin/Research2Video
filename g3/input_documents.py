"""Local document extraction and immutable upload provenance; no model calls."""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID, uuid4
import zipfile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

# Bound input sizes for local parsing; these are upload limits, not video limits.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 64 * 1024 * 1024
SUFFIXES = {".docx", ".pdf", ".txt", ".md"}


class DocumentError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise DocumentError("DOCUMENT_EMPTY", "文件中没有可用正文，请换一份含文字的文档。")
    if "\ufffd" in text or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise DocumentError("DOCUMENT_TEXT_INVALID", "提取结果含乱码或二进制字符，请另存为 UTF-8 文本后上传。")
    return text


def _word_blocks(container):
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                yield block.text
        elif isinstance(block, Table):
            for row in block.rows:
                cells, seen = [], set()
                for cell in row.cells:
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    cells.append(" / ".join(_word_blocks(cell)))
                yield "\t".join(cells)


def extract_document(filename: str, raw: bytes) -> tuple[str, dict]:
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").split("/")[-1])
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise DocumentError("DOCUMENT_TYPE_UNSUPPORTED", "支持 .docx、文字型 .pdf、.txt 和 .md；旧版 .doc 请另存为 .docx。")
    if not raw:
        raise DocumentError("DOCUMENT_EMPTY", "文件是空的，请选择含正文的文件。")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise DocumentError("DOCUMENT_TOO_LARGE", "单个文件最大 32 MB；可拆分文档后上传。")
    warnings, extra = [], {}
    try:
        if suffix in {".txt", ".md"}:
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                encoding = "gb18030"
                text = raw.decode(encoding)
            extra["encoding"] = encoding
        elif suffix == ".docx":
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                if sum(i.file_size for i in archive.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                    raise DocumentError("DOCUMENT_TOO_LARGE", "Word 解压内容过大，请拆分文档后上传。")
            document = Document(BytesIO(raw))
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            if any(e.tag in {namespace + "ins", namespace + "del"} for e in document.element.iter()):
                raise DocumentError("DOCUMENT_TRACKED_CHANGES", "Word 含未处理的修订，请先接受或拒绝修订并保存，避免正文歧义。")
            text = "\n\n".join(_word_blocks(document))
            warnings.append("已提取正文和表格；页眉页脚、批注、脚注、文本框和图片内容不自动转录。")
        else:
            if not raw.startswith(b"%PDF-"):
                raise DocumentError("DOCUMENT_INVALID", "文件不是有效 PDF，请检查格式。")
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted:
                raise DocumentError("DOCUMENT_ENCRYPTED", "PDF 已加密，请解除密码后上传。")
            pages = []
            for number, page in enumerate(reader.pages, 1):
                content = page.extract_text() or ""
                if not content.strip() and len(page.images):
                    raise DocumentError("PDF_OCR_REQUIRED", f"PDF 第 {number} 页没有可提取文字，可能是扫描件。本版暂不支持 OCR，请先转成文字。")
                if content.strip():
                    pages.append(content)
                else:
                    warnings.append(f"第 {number} 页未提取到正文，请留意该页是否含非文字内容。")
            text = "\n\n".join(pages)
            extra["page_count"] = len(reader.pages)
            warnings.append("已提取 PDF 文字；图片、图表不自动转录，复杂分栏或表格的阅读顺序可在正文中调整。")
        text = _check_text(text)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("DOCUMENT_INVALID", "无法读取这份文件，请检查是否损坏、编码异常，或另存为标准文档后重试。") from exc
    return text, {"filename": filename, "format": suffix[1:], "bytes": len(raw),
                  "original_sha256": digest(raw), "extracted_sha256": digest(text.encode()),
                  "char_count": len(text), "warnings": warnings, **extra}


def save_import(data_dir: Path, filename: str, raw: bytes) -> dict:
    text, metadata = extract_document(filename, raw)
    document_id = str(uuid4())
    root = data_dir / "input-documents" / document_id
    root.mkdir(parents=True, exist_ok=False)
    (root / f"original.{metadata['format']}").write_bytes(raw)
    (root / "extracted.txt").write_text(text, encoding="utf-8")
    (root / "document.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"document_id": document_id, "text": text, **metadata}


def read_document(root: Path) -> dict:
    """Read only fixed, hash-verified files beneath a trusted job/import root."""
    try:
        if root.is_symlink():
            raise ValueError("symlink root")
        metadata_path = root / "document.json"
        if metadata_path.is_symlink():
            raise ValueError("symlink metadata")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if "." + metadata["format"] not in SUFFIXES:
            raise ValueError("invalid format")
        original = root / f"original.{metadata['format']}"
        extracted = root / "extracted.txt"
        if original.is_symlink() or extracted.is_symlink():
            raise ValueError("symlink file")
        raw, text_raw = original.read_bytes(), extracted.read_bytes()
        if digest(raw) != metadata["original_sha256"] or digest(text_raw) != metadata["extracted_sha256"]:
            raise ValueError("changed upload")
        return {"metadata": metadata, "raw": raw, "text": text_raw.decode("utf-8"), "path": original}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DocumentError("DOCUMENT_UNAVAILABLE", "上传的原文档或提取记录不可用，请重新上传；当前正文仍保留在编辑框中。") from exc


def load_import(data_dir: Path, document_id: str) -> dict:
    try:
        document_id = str(UUID(document_id))
    except ValueError as exc:
        raise DocumentError("DOCUMENT_UNAVAILABLE", "文档标识无效，请重新上传。") from exc
    return read_document(data_dir / "input-documents" / document_id)
