"""Private local uploads and bounded document fragments, without network access.

An attachment's scope is immutable. Resolving it only supplies model material;
the caller must still apply its existing endpoint, budget and source gates.
"""
import base64
import binascii
from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import threading
from urllib.parse import unquote, urlsplit
import warnings
import xml.etree.ElementTree as ET
import zipfile

from town_security import linked
from town_store import TownError, now, pack, uid

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_SIDE = 1280
MAX_IMAGE_BYTES = 512 * 1024
MAX_IMAGES = 128
MAX_PDF_PAGES = 80
MAX_TEXT_BYTES = 1024 * 1024
MAX_FRAGMENT_TEXT_BYTES = 48000
MAX_FRAGMENTS = 512
MAX_METADATA_BYTES = 6 * 1024 * 1024
MAX_DOCUMENTS = 1024
MAX_STORE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ATTACHMENTS = 16
MAX_RESOLVE_ATTACHMENTS = 4
MAX_RESOLVE_IMAGES = 8
MAX_RESOLVE_TEXT_BYTES = 64 * 1024
MAX_RESOLVE_IMAGE_BYTES = 4 * 1024 * 1024
MIMES = {"txt": "text/plain", "md": "text/plain", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _id(value, label="附件编号"):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise TownError(400, label + "无效")
    return value


def _pillow():
    try:
        from PIL import Image, ImageOps
        return Image, ImageOps
    except ImportError:
        raise TownError(503, "此安装缺少图片解析组件 Pillow，请更新完整安装包") from None


def _pdfium():
    try:
        import pypdfium2
        return pypdfium2
    except ImportError:
        raise TownError(503, "此安装缺少 PDF 解析组件 pypdfium2，请更新完整安装包") from None


class Documents:
    def __init__(self, app):
        self.app = app
        self.data_dir = Path(app.data_dir).absolute()
        self.root = self.data_dir / "documents"
        self.lock = threading.RLock()
        self._safe(self.root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with app.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS v2_documents(
                    id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,project_id TEXT,
                    classification TEXT NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,
                    metadata_sha256 TEXT NOT NULL,total_bytes INTEGER NOT NULL,created_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS v2_documents_immutable BEFORE UPDATE ON v2_documents
                    BEGIN SELECT RAISE(ABORT,'immutable attachment'); END;
            """)

    def _safe(self, path):
        path = Path(path).absolute()
        if path != self.root and self.root not in path.parents:
            raise TownError(403, "附件路径超出私有存储范围")
        for parent in (path, *path.parents):
            if parent.is_symlink() or parent.exists() and linked(parent):
                raise TownError(503, "附件存储路径不能包含链接")
            if parent.exists() and parent != path and not parent.is_dir():
                raise TownError(503, "附件存储父路径不是目录")
        if path.exists() and (not (path.is_dir() or path.is_file())
                              or path.is_file() and path.stat().st_nlink != 1):
            raise TownError(503, "附件必须使用独立的普通文件")
        return path

    def _read_file(self, path, limit):
        path = self._safe(path)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise TownError(409, "附件文件类型或大小校验失败")
                value = stream.read(limit + 1)
                if len(value) != info.st_size:
                    raise TownError(409, "附件文件在读取时发生变化")
                return value
        except OSError:
            raise TownError(409, "附件文件不可读取") from None

    def _write_file(self, path, content):
        path = self._safe(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _project(self, project_id):
        _id(project_id, "项目编号")
        development = getattr(self.app, "development", None)
        if development is None:
            raise TownError(409, "研发项目服务尚未就绪")
        return development.project(project_id)

    def _agent(self, agent_id):
        if not isinstance(agent_id, str) or not agent_id or len(agent_id) > 100:
            raise TownError(400, "请选择附件所属居民")
        with self.app.db() as db:
            self.app._agent(db, agent_id)

    def _upload_scope(self, data):
        classification = data.get("classification", "private")
        if classification not in ("requirement", "private"):
            raise TownError(400, "请选择业务需求资料或居民私有资料")
        project_id = data.get("project_id")
        project = self._project(project_id) if project_id is not None else None
        agent_id = data.get("agent_id")
        if classification == "requirement":
            if project is None:
                raise TownError(400, "共享需求附件必须绑定研发项目")
            agent_id = agent_id or project["members"]["product"]
            if agent_id not in [project["members"][role] for role in ("product", "developer", "tester")]:
                raise TownError(403, "只有本项目产品、开发和测试居民可接收需求原件")
        elif project is not None and agent_id not in project["members"].values():
            raise TownError(403, "私有附件所属居民不在此项目中")
        self._agent(agent_id)
        return dict(classification=classification, agent_id=agent_id, project_id=project_id)

    def _authorize(self, row, agent_id, project_id):
        self._agent(agent_id)
        if row["classification"] == "private":
            if row["agent_id"] != agent_id or row["project_id"] is not None and row["project_id"] != project_id:
                raise TownError(403, "这份私有附件不在当前居民和项目范围内")
            if row["project_id"] is not None and agent_id not in self._project(project_id)["members"].values():
                raise TownError(403, "该居民已不在附件绑定项目中")
        elif row["classification"] == "requirement":
            if project_id != row["project_id"]:
                raise TownError(403, "需求附件不能跨项目读取")
            members = self._project(project_id)["members"]
            if agent_id not in [members[role] for role in ("product", "developer", "tester")]:
                raise TownError(403, "只有本项目产品、开发和测试居民可读取需求原件")
        else:
            raise TownError(409, "附件范围记录无效")

    def _row(self, identity):
        _id(identity)
        with self.app.db() as db:
            row = db.execute("SELECT * FROM v2_documents WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise TownError(404, "没有找到这份附件")
        return dict(row)

    def _metadata(self, row):
        content = self._read_file(self.root / row["id"] / "metadata.json", MAX_METADATA_BYTES)
        if _hash(content) != row["metadata_sha256"]:
            raise TownError(409, "附件元数据指纹已变化")
        try:
            value = json.loads(content)
            if (value["id"] != row["id"] or value["sha256"] != row["sha256"] or value["bytes"] != row["bytes"]
                    or value["scope"] != {key: row[key] for key in ("classification", "agent_id", "project_id")}):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise TownError(409, "附件元数据结构无效") from None
        return value

    @staticmethod
    def _warning(state, message):
        if message not in state["warnings"]:
            state["warnings"].append(message)
        state["status"] = "partial"

    def _fragment(self, state, label, content="", image_id=None):
        if len(state["fragments"]) >= MAX_FRAGMENTS:
            self._warning(state, "文档片段超过 512 个，后续片段已跳过；请拆分后继续上传")
            return
        if len(content.encode("utf-8")) > MAX_FRAGMENT_TEXT_BYTES:
            content = content.encode("utf-8")[:MAX_FRAGMENT_TEXT_BYTES].decode("utf-8", errors="ignore")
            self._warning(state, label[:200] + "文字超过单片段上限，后续文字已截断；请结合页图核对")
        remaining = MAX_TEXT_BYTES - state["text_bytes"]
        raw = content.encode("utf-8")
        if len(raw) > remaining:
            content = raw[:max(0, remaining)].decode("utf-8", errors="ignore")
            self._warning(state, "提取文字超过 1 MiB，后续文字已截断；请核对原件并拆分上传")
        state["text_bytes"] += len(content.encode("utf-8"))
        if not content.strip() and image_id is None:
            return
        item = dict(id=uid(), label=label[:240], text=content)
        if image_id is not None:
            item["image_id"] = image_id
        state["fragments"].append(item)

    def _image(self, state, raw=None, expected=None, pil_image=None):
        if len(state["images"]) >= MAX_IMAGES:
            self._warning(state, "图片超过 128 幅，后续图片已跳过；请拆分上传")
            return None
        Image, ImageOps = _pillow()
        opened = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                opened = Image.open(io.BytesIO(raw)) if pil_image is None else pil_image
                if expected is not None and opened.format != expected:
                    raise TownError(400, "图片实际类型与文件扩展名不一致")
                if (opened.width <= 0 or opened.height <= 0 or opened.width * opened.height > MAX_IMAGE_PIXELS
                        or max(opened.size) > 32768):
                    raise TownError(413, "图片像素超过 2400 万或尺寸过大，请先缩小图片")
                if getattr(opened, "n_frames", 1) != 1:
                    raise TownError(400, "暂不接受动画图片，请上传单帧截图")
                opened.load()
                transposed = ImageOps.exif_transpose(opened)
                try:
                    transposed.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
                    clean = Image.new("RGB", transposed.size, "white")
                    if transposed.mode in ("RGBA", "LA") or "transparency" in transposed.info:
                        rgba = transposed.convert("RGBA")
                        clean.paste(rgba, mask=rgba.getchannel("A"))
                        rgba.close()
                    else:
                        clean.paste(transposed.convert("RGB"))
                finally:
                    transposed.close()
                try:
                    for quality in (85, 70, 55):
                        output = io.BytesIO()
                        clean.save(output, format="JPEG", quality=quality, optimize=True)
                        encoded = output.getvalue()
                        if len(encoded) <= MAX_IMAGE_BYTES:
                            break
                    if len(encoded) > MAX_IMAGE_BYTES:
                        raise TownError(413, "图片清洗后仍然过大，请先缩小图片")
                    identity = uid()
                    item = dict(id=identity, mime="image/jpeg", width=clean.width, height=clean.height,
                                bytes=len(encoded), sha256=_hash(encoded))
                finally:
                    clean.close()
        except TownError:
            raise
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise TownError(400, "图片解码失败或像素超出安全范围") from None
        finally:
            if opened is not None and pil_image is None:
                opened.close()
        state["images"].append(item)
        state["image_bytes"][identity] = encoded
        return identity

    def _text(self, state, raw):
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeError:
            raise TownError(400, "文字附件须使用 UTF-8 编码") from None
        if any(ord(char) < 32 and char not in "\n\r\t" for char in content):
            raise TownError(400, "文字附件包含二进制控制字符")
        for index in range(0, len(content), 6000):
            self._fragment(state, "文字片段 %d" % (index // 6000 + 1), content[index:index + 6000])
            if state["text_bytes"] >= MAX_TEXT_BYTES:
                if index + 6000 < len(content):
                    self._warning(state, "提取文字超过 1 MiB，后续文字已截断；请核对原件并拆分上传")
                break

    @staticmethod
    def _xml(raw):
        if len(raw) > 8 * 1024 * 1024:
            raise TownError(413, "DOCX 单个结构文件过大，请拆分文档")
        try:
            value = raw.decode("utf-8-sig")
            if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", value, re.I):
                raise TownError(400, "DOCX 不允许包含外部实体或文档类型声明")
            result = ET.fromstring(value)
            if sum(1 for _ in result.iter()) > 100000:
                raise TownError(413, "DOCX 结构过于复杂，请拆分文档")
            return result
        except (UnicodeError, ET.ParseError):
            raise TownError(400, "DOCX 结构文件无效") from None

    def _docx(self, state, raw):
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise TownError(400, "DOCX 压缩结构无效") from None
        with archive:
            entries = archive.infolist()
            if len(entries) > 512 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise TownError(413, "DOCX 解压内容超过 100 MiB 或文件数过多")
            names = set()
            for item in entries:
                name = item.filename
                if (name in names or "\\" in name or "\x00" in name or name.startswith("/")
                        or any(part in ("..", ".") for part in name.rstrip("/").split("/"))
                        or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1
                        or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise TownError(400, "DOCX 含不安全的压缩条目")
                if item.file_size > MAX_UPLOAD_BYTES or item.file_size > max(1, item.compress_size) * 200:
                    raise TownError(413, "DOCX 压缩比例或单项大小超出安全范围")
                if name.lower().endswith((".bin", ".exe", ".dll", ".js", ".html", ".htm", ".svg")):
                    raise TownError(400, "DOCX 不允许包含宏、可执行内容或嵌入网页")
                names.add(name)
            if not {"[Content_Types].xml", "word/document.xml"} <= names:
                raise TownError(400, "不是有效的 DOCX 文档")
            types = self._xml(archive.read("[Content_Types].xml"))
            if any("macro" in str(node.attrib).lower() or "vba" in str(node.attrib).lower() for node in types):
                raise TownError(400, "DOCX 不允许包含宏")
            image_rels = {}
            for name in sorted(names):
                if not name.endswith(".rels"):
                    continue
                rels = self._xml(archive.read(name))
                base = "" if name == "_rels/.rels" else posixpath.dirname(posixpath.dirname(name))
                seen = set()
                for relation in rels:
                    target = unquote(relation.get("Target", ""))
                    parsed = urlsplit(target)
                    target_path = posixpath.normpath(posixpath.join(base, target))
                    if (relation.tag != REL + "Relationship" or relation.get("Id") in seen
                            or relation.get("TargetMode", "Internal") != "Internal" or parsed.scheme or parsed.netloc
                            or not target or "\\" in target or "\x00" in target or target.startswith("/")
                            or target_path == ".." or target_path.startswith("../")):
                        raise TownError(400, "DOCX 含外部关系或不安全的资源地址")
                    seen.add(relation.get("Id"))
                    if name == "word/_rels/document.xml.rels" and relation.get("Type", "").endswith("/image"):
                        if target_path not in names:
                            raise TownError(400, "DOCX 图片引用缺失")
                        image_rels[relation.get("Id")] = target_path
            document = self._xml(archive.read("word/document.xml"))
            if any(node.tag in (W + "altChunk", W + "object") for node in document.iter()):
                raise TownError(400, "DOCX 不支持嵌入对象或外部文档")
            body = document.find(W + "body")
            if body is None:
                raise TownError(400, "DOCX 缺少正文")
            section, pending, cache = "文档正文", [], {}
            def flush(image_id=None):
                nonlocal pending
                content = "\n".join(pending)
                for start in range(0, max(1, len(content)), 6000):
                    self._fragment(state, section, content[start:start + 6000], image_id if start == 0 else None)
                pending = []
            def paragraph_text(paragraph):
                return "".join((node.text or "") if node.tag == W + "t" else "\t" if node.tag == W + "tab" else "\n"
                               for node in paragraph.iter() if node.tag in (W + "t", W + "tab", W + "br"))
            def paragraph_images(paragraph):
                for image in paragraph.iter():
                    if image.tag not in (A + "blip", "{urn:schemas-microsoft-com:vml}imagedata"):
                        continue
                    if image.get(R + "link") or image.get("src"):
                        raise TownError(400, "DOCX 不允许外链图片")
                    target = image_rels.get(image.get(R + "embed", image.get(R + "id")))
                    if target is None:
                        raise TownError(400, "DOCX 图片关系缺失")
                    if target not in cache:
                        suffix = PurePosixPath(target).suffix.lower()
                        expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(suffix)
                        if expected is None:
                            self._warning(state, "DOCX 含不支持的图片格式；对应图片已跳过，请另传 PNG/JPEG/WebP")
                            cache[target] = None
                        else:
                            cache[target] = self._image(state, archive.read(target), expected)
                    flush(cache[target])
            for child in body:
                if child.tag == W + "tbl":
                    pending.append("表格：")
                    for row in child.findall(W + "tr"):
                        pending.append(" | ".join(" / ".join(paragraph_text(paragraph) for paragraph in cell.iter(W + "p"))
                                                  for cell in row.findall(W + "tc")))
                        paragraph_images(row)
                    flush()
                    continue
                paragraphs = [child] if child.tag == W + "p" else list(child.iter(W + "p"))
                for paragraph in paragraphs:
                    content = paragraph_text(paragraph)
                    style = paragraph.find(W + "pPr/" + W + "pStyle")
                    if style is not None and re.search(r"heading|标题", style.get(W + "val", ""), re.I):
                        flush()
                        section = content.strip() or "未命名章节"
                    if content.strip():
                        pending.append(content)
                    paragraph_images(paragraph)
                    if sum(len(value) for value in pending) > 6000:
                        flush()
            flush()
            if any(name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments"))
                   and name.endswith(".xml") for name in names):
                self._warning(state, "仅提取 DOCX 正文、表格和正文图片；页眉页脚、脚注及批注未纳入分析")

    def _pdf(self, state, raw):
        pdfium = _pdfium()
        _pillow()
        document = None
        try:
            document = pdfium.PdfDocument(raw)
            state["pages"] = len(document)
            if not state["pages"]:
                raise TownError(400, "PDF 没有可读取的页面")
            if state["pages"] > MAX_PDF_PAGES:
                self._warning(state, "PDF 共 %d 页，仅解析前 80 页；第 81 至 %d 页已跳过，请拆分上传" % (state["pages"], state["pages"]))
            for index in range(min(state["pages"], MAX_PDF_PAGES)):
                page = textpage = bitmap = pil_image = None
                try:
                    page = document[index]
                    width, height = page.get_size()
                    if not all(math.isfinite(value) and 0 < value <= 14400 for value in (width, height)):
                        raise TownError(413, "PDF 页面尺寸超出安全范围")
                    textpage = page.get_textpage()
                    count = textpage.count_chars()
                    content = textpage.get_text_range(0, min(count, 24000)) if count else ""
                    if count > 24000:
                        self._warning(state, "PDF 第 %d 页文字过多，仅保留前 24000 个字符，页图仍可核对" % (index + 1))
                    bitmap = page.render(scale=min(2.0, MAX_IMAGE_SIDE / max(width, height)))
                    pil_image = bitmap.to_pil()
                    image_id = self._image(state, pil_image=pil_image)
                    self._fragment(state, "第 %d 页" % (index + 1), content, image_id)
                except TownError:
                    raise
                except Exception:
                    self._warning(state, "PDF 第 %d 页读取失败，已跳过；请核对原件并另传该页" % (index + 1))
                finally:
                    for resource in (pil_image, bitmap, textpage, page):
                        if resource is not None:
                            resource.close()
        except TownError:
            raise
        except Exception:
            raise TownError(400, "PDF 无法解析，可能损坏、加密或不受支持") from None
        finally:
            if document is not None:
                document.close()

    def upload(self, data):
        if not isinstance(data, dict) or set(data) - {"name", "content_base64", "agent_id", "project_id", "classification"}:
            raise TownError(400, "附件只接受文件名和本地上传内容，不接受路径或远程地址")
        name = data.get("name")
        try:
            valid_name = (isinstance(name, str) and bool(name.strip()) and len(name.encode("utf-8")) <= 240
                          and not any(char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in name))
        except UnicodeError:
            valid_name = False
        if not valid_name:
            raise TownError(400, "附件文件名无效")
        kind = Path(name).suffix.lower().lstrip(".")
        kind = "jpeg" if kind == "jpg" else kind
        if kind not in MIMES:
            raise TownError(400, "支持 TXT、MD、DOCX、PDF、PNG、JPEG 和 WebP 附件")
        encoded = data.get("content_base64")
        if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
            raise TownError(413, "附件为空或超过 20 MiB 上限")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise TownError(400, "附件编码无效") from None
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise TownError(413, "附件为空或超过 20 MiB 上限")
        scope = self._upload_scope(data)
        fingerprint = _hash(raw)
        with self.app.lock, self.lock:
            self._safe(self.root)
            with self.app.db() as db:
                if scope["classification"] == "requirement" and db.execute(
                        "SELECT 1 FROM v2_documents WHERE sha256=? AND classification='private'", (fingerprint,)).fetchone():
                    raise TownError(403, "此文件已标记为私有资料，不能升级为项目共享需求")
                previous = db.execute("SELECT * FROM v2_documents WHERE sha256=? AND agent_id=? AND project_id IS ? AND classification=?",
                                      (fingerprint, scope["agent_id"], scope["project_id"], scope["classification"])).fetchone()
                count, total = db.execute("SELECT COUNT(*),COALESCE(SUM(total_bytes),0) FROM v2_documents").fetchone()
            if previous:
                return self.read(previous["id"], scope["agent_id"], scope["project_id"])
            if count >= MAX_DOCUMENTS or total + len(raw) > MAX_STORE_BYTES:
                raise TownError(413, "本地附件数量或总容量已达上限")
            state = dict(status="ready", fragments=[], images=[], warnings=[], pages=None, text_bytes=0, image_bytes={})
            try:
                if kind in ("txt", "md"):
                    self._text(state, raw)
                elif kind == "docx":
                    self._docx(state, raw)
                elif kind == "pdf":
                    self._pdf(state, raw)
                else:
                    image_id = self._image(state, raw, {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[kind])
                    self._fragment(state, name, "", image_id)
            except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as error:
                if isinstance(error, TownError):
                    raise
                raise TownError(400, "附件解析失败，请检查文件完整性或改用受支持格式") from None
            if not state["fragments"]:
                raise TownError(422, "附件没有可读取的文字或图片，请检查原件")
            identity = uid()
            metadata = dict(id=identity, name=name, type=kind, mime=MIMES[kind], status=state["status"],
                            bytes=len(raw), sha256=fingerprint, scope=scope, fragments=state["fragments"],
                            images=state["images"], assets=deepcopy(state["images"]), warnings=state["warnings"],
                            pages=state["pages"], preview_text="\n".join(f["text"] for f in state["fragments"])[:4000], created_at=now())
            metadata_bytes = pack(metadata).encode("utf-8")
            total_bytes = len(raw) + len(metadata_bytes) + sum(len(value) for value in state["image_bytes"].values())
            if len(metadata_bytes) > MAX_METADATA_BYTES or total + total_bytes > MAX_STORE_BYTES:
                raise TownError(413, "附件解析结果或本地总容量超出上限")
            pending = self._safe(self.root / (identity + ".pending"))
            target = self._safe(self.root / identity)
            pending.mkdir(mode=0o700)
            published = False
            try:
                self._write_file(pending / ("original." + kind), raw)
                for image_id, image_raw in state["image_bytes"].items():
                    self._write_file(pending / (image_id + ".jpg"), image_raw)
                self._write_file(pending / "metadata.json", metadata_bytes)
                self._safe(pending)
                pending.rename(target)
                with self.app.db() as db:
                    db.execute("INSERT INTO v2_documents VALUES (?,?,?,?,?,?,?,?,?)",
                               (identity, scope["agent_id"], scope["project_id"], scope["classification"], fingerprint,
                                len(raw), _hash(metadata_bytes), total_bytes, metadata["created_at"]))
                published = True
            finally:
                if not published:
                    for directory in (pending, target):
                        if directory.exists() and not directory.is_symlink():
                            shutil.rmtree(directory)
            return metadata

    def read(self, identity, agent_id, project_id=None):
        row = self._row(identity)
        self._authorize(row, agent_id, project_id)
        value = self._metadata(row)
        raw = self._read_file(self.root / identity / ("original." + value["type"]), MAX_UPLOAD_BYTES)
        if len(raw) != row["bytes"] or _hash(raw) != row["sha256"]:
            raise TownError(409, "附件原件指纹已变化，不能继续分析")
        return value

    def list(self, agent_id, project_id=None, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TownError(400, "附件列表每次最多读取 100 条")
        self._agent(agent_id)
        if project_id is not None:
            self._project(project_id)
        with self.app.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM v2_documents WHERE agent_id=? OR project_id=? ORDER BY created_at,id",
                                                   (agent_id, project_id))]
        result = []
        for row in rows:
            try:
                self._authorize(row, agent_id, project_id)
            except TownError as error:
                if error.code == 403:
                    continue
                raise
            item = self._metadata(row)
            item["fragment_count"] = len(item["fragments"])
            item["fragments"] = [{key: value for key, value in fragment.items() if key != "text"} for fragment in item["fragments"]]
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def asset(self, identity, image_id=None, *, agent_id, project_id=None):
        metadata = self.read(identity, agent_id, project_id)
        if image_id is None:
            return dict(path=self._safe(self.root / identity / ("original." + metadata["type"])), mime=metadata["mime"],
                        name=metadata["name"], sha256=metadata["sha256"], bytes=metadata["bytes"], disposition="attachment")
        _id(image_id, "图片编号")
        image = next((item for item in metadata["images"] if item["id"] == image_id), None)
        if image is None:
            raise TownError(404, "附件中没有这幅图片")
        path = self._safe(self.root / identity / (image_id + ".jpg"))
        content = self._read_file(path, MAX_IMAGE_BYTES)
        if len(content) != image["bytes"] or _hash(content) != image["sha256"]:
            raise TownError(409, "附件图片指纹已变化")
        return dict(path=path, mime=image["mime"], name=image_id + ".jpg", sha256=image["sha256"],
                    bytes=image["bytes"], disposition="inline")

    def resolve(self, ids, agent_id, project_id=None, endpoint=None, *, fragment_ids=None):
        # endpoint intentionally confers no access. The caller owns network gates.
        if (not isinstance(ids, (list, tuple)) or not ids or len(ids) > MAX_RESOLVE_ATTACHMENTS
                or any(not isinstance(identity, str) for identity in ids) or len(set(ids)) != len(ids)):
            raise TownError(400, "每步请选择 1 至 4 份不重复附件")
        if fragment_ids is not None and (not isinstance(fragment_ids, dict) or set(fragment_ids) != set(ids)):
            raise TownError(400, "片段选择必须逐一对应本步附件")
        result, image_count, image_bytes, text_bytes = [], 0, 0, 0
        for identity in ids:
            metadata = self.read(identity, agent_id, project_id)
            fragments = metadata["fragments"]
            if fragment_ids is not None:
                selected = fragment_ids[identity]
                if (not isinstance(selected, (list, tuple)) or not selected or len(selected) > MAX_FRAGMENTS
                        or any(not isinstance(item, str) for item in selected) or len(set(selected)) != len(selected)
                        or not set(selected) <= {item["id"] for item in fragments}):
                    raise TownError(400, "选中的文档片段不存在或重复")
                fragments = [item for item in fragments if item["id"] in selected]
            images = {item["id"]: item for item in metadata["images"]}
            chosen, rendered, remaining = [], {}, []
            for fragment in fragments:
                size = len(fragment["text"].encode("utf-8"))
                image_id = fragment.get("image_id")
                new_image = images.get(image_id) if image_id not in rendered else None
                if (text_bytes + size > MAX_RESOLVE_TEXT_BYTES or new_image is not None and
                        (image_count >= MAX_RESOLVE_IMAGES or image_bytes + new_image["bytes"] > MAX_RESOLVE_IMAGE_BYTES)):
                    remaining.append(fragment["id"])
                    continue
                if new_image is not None:
                    asset = self.asset(identity, image_id, agent_id=agent_id, project_id=project_id)
                    content = self._read_file(asset["path"], MAX_IMAGE_BYTES)
                    if _hash(content) != asset["sha256"]:
                        raise TownError(409, "图片在读取时发生变化")
                    rendered[image_id] = dict(new_image, content_base64=base64.b64encode(content).decode("ascii"))
                    image_count += 1
                    image_bytes += len(content)
                chosen.append(fragment)
                text_bytes += size
            metadata["fragments"] = chosen
            metadata["images"] = list(rendered.values())
            metadata["assets"] = [{key: value for key, value in image.items() if key != "content_base64"} for image in rendered.values()]
            metadata["preview_text"] = "\n".join(item["text"] for item in chosen)[:4000]
            metadata["remaining_fragment_ids"] = remaining
            if remaining:
                metadata["warnings"].append("本步已达到文字或图片上限，剩余片段需在后续步骤继续读取")
            result.append(metadata)
        return result
