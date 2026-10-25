"""Immutable article imports and working copies, kept outside research reports."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zipfile import ZipFile

MAX_BYTES = 32 * 1024 * 1024
MAX_CHARS = 60000
SUFFIXES = {".txt", ".md", ".docx", ".pdf"}


class ArticleError(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value or len(value) > MAX_CHARS:
        raise ArticleError(f"文章须有正文，且不超过 {MAX_CHARS} 字符；长文章请分篇处理")
    if "\ufffd" in value or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ArticleError("正文含乱码或二进制字符，请检查提取结果")
    return value


def extract(filename: str, raw: bytes) -> tuple[str, list[str]]:
    """Same supported formats and extraction limits as AutoVideo's document input."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUFFIXES:
        raise ArticleError("支持 TXT、Markdown、Word（.docx）和文字型 PDF")
    if not raw or len(raw) > MAX_BYTES:
        raise ArticleError("文件为空或超过 32 MB，请拆分后上传")
    warnings = []
    try:
        if suffix in {".txt", ".md"}:
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            try:
                body = raw.decode(encoding)
            except UnicodeDecodeError:
                body = raw.decode("gb18030")
        elif suffix == ".docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            with ZipFile(BytesIO(raw)) as archive:
                if sum(p.file_size for p in archive.infolist()) > 64 * 1024 * 1024:
                    raise ArticleError("Word 解压内容过大，请拆分后上传")
            document = Document(BytesIO(raw))
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            if any(e.tag in {namespace + "ins", namespace + "del"} for e in document.element.iter()):
                raise ArticleError("Word 含未处理的修订，请先接受或拒绝修订并保存")

            def blocks(container):
                for block in container.iter_inner_content():
                    if isinstance(block, Paragraph) and block.text.strip():
                        yield block.text
                    elif isinstance(block, Table):
                        for row in block.rows:
                            seen, cells = set(), []
                            for cell in row.cells:
                                if cell._tc not in seen:
                                    seen.add(cell._tc)
                                    cells.append(" / ".join(blocks(cell)))
                            yield "\t".join(cells)

            body = "\n\n".join(blocks(document))
            warnings.append("已提取正文和表格；图片、文本框、批注、脚注和页眉页脚不自动转录，请检查正文。")
        else:
            from pypdf import PdfReader
            if not raw.startswith(b"%PDF-"):
                raise ArticleError("文件不是有效 PDF")
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted:
                raise ArticleError("请解除 PDF 密码后上传")
            pages = []
            for index, page in enumerate(reader.pages, 1):
                body = page.extract_text() or ""
                if not body.strip() and len(page.images):
                    raise ArticleError(f"PDF 第 {index} 页需要 OCR，请先转成文字；本次未导入不完整正文")
                if not body.strip():
                    warnings.append(f"第 {index} 页未提取到文字，请检查是否遗漏内容。")
                pages.append(body)
            body = "\n\n".join(pages)
            warnings.append("已提取 PDF 文字；请检查分栏、表格顺序，图片不自动转录。")
        return text(body), warnings
    except ArticleError:
        raise
    except ImportError as exc:
        raise ArticleError("缺少文档解析依赖，请安装项目 requirements.txt 后重试") from exc
    except Exception as exc:
        raise ArticleError("无法读取文件，请检查格式、编码或文件是否损坏") from exc


def _root(state_dir: str, identifier: str) -> Path:
    try:
        key = str(UUID(identifier))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ArticleError("文章标识无效") from exc
    root = Path(state_dir) / "video_articles" / key
    if root.is_symlink():
        raise ArticleError("文章存储路径无效")
    return root


def save_import(state_dir: str, filename: str, raw: bytes) -> dict:
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").split("/")[-1])
    body, warnings = extract(filename, raw)
    identifier = str(uuid4())
    root = _root(state_dir, identifier)
    root.mkdir(parents=True, exist_ok=False)
    suffix = Path(filename).suffix.lower()
    (root / f"original{suffix}").write_bytes(raw)
    (root / "extracted.txt").write_text(body, encoding="utf-8")
    record = {"id": identifier, "filename": filename, "suffix": suffix, "original_sha256": digest(raw),
              "extracted_sha256": digest(body.encode()), "chars": len(body), "warnings": warnings}
    (root / "import.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return {**record, "text": body}


def read_import(state_dir: str, identifier: str) -> dict:
    root = _root(state_dir, identifier)
    try:
        path = root / "import.json"
        if path.is_symlink():
            raise ValueError
        record = json.loads(path.read_text())
        if record["id"] != identifier or record["suffix"] not in SUFFIXES:
            raise ValueError
        original, extracted = root / f"original{record['suffix']}", root / "extracted.txt"
        if original.is_symlink() or extracted.is_symlink():
            raise ValueError
        if digest(original.read_bytes()) != record["original_sha256"] or digest(extracted.read_bytes()) != record["extracted_sha256"]:
            raise ValueError
        return {**record, "text": extracted.read_text(), "original_path": str(original)}
    except (OSError, ValueError, KeyError) as exc:
        raise ArticleError("文章原始文件或提取记录不可用，请重新上传") from exc


def snapshot(state_dir: str, *, body: str, title: str, import_id: str | None = None,
             author: str = "", source_url: str = "", published_on: str = "", origin: str = "copied") -> dict:
    body = text(body)
    if source_url:
        try:
            url = urlsplit(source_url)
            if url.scheme not in {"http", "https"} or not url.hostname or any(c.isspace() for c in source_url):
                raise ValueError
        except ValueError as exc:
            raise ArticleError("来源链接须为 http(s) 网址") from exc
    if published_on:
        try:
            if date.fromisoformat(published_on).isoformat() != published_on:
                raise ValueError
        except ValueError as exc:
            raise ArticleError("发布日期须为 YYYY-MM-DD，不知道可以留空") from exc
    if origin not in {"original", "copied", "ai"}:
        raise ArticleError("未知文章来源类型")
    imported = read_import(state_dir, import_id) if import_id else save_import(state_dir, "粘贴文章.txt", body.encode())
    identifier = str(uuid4())
    root = _root(state_dir, identifier)
    root.mkdir(parents=True, exist_ok=False)
    (root / "working.txt").write_text(body, encoding="utf-8")
    record = {"id": identifier, "title": title.strip() or "文章朗读", "import_id": imported["id"],
              "sha256": digest(body.encode()), "chars": len(body), "author": author.strip(),
              "original_sha256": imported["original_sha256"], "extracted_sha256": imported["extracted_sha256"],
              "source_url": source_url, "published_on": published_on, "origin": origin,
              "edited_after_import": digest(body.encode()) != imported["extracted_sha256"]}
    (root / "source.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def read(state_dir: str, reference: dict) -> dict:
    root = _root(state_dir, reference.get("id"))
    try:
        if (root / "source.json").is_symlink() or (root / "working.txt").is_symlink():
            raise ValueError
        record = json.loads((root / "source.json").read_text())
        body = (root / "working.txt").read_text()
        if record != reference or digest(body.encode()) != record["sha256"]:
            raise ValueError
        imported = read_import(state_dir, record["import_id"])
        if any(imported[key] != record[key] for key in ("original_sha256", "extracted_sha256")):
            raise ValueError
        return {**record, "text": body}
    except (OSError, ValueError, KeyError) as exc:
        raise ArticleError("已保存的文章原文或哈希不一致，请重新导入；现有文稿和成片仍保留") from exc
