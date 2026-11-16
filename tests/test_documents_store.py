"""Local attachment security and parsing: temporary synthetic files only."""
import base64
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import town_documents as documents
from town_documents import Documents
from town_store import TownError

PROJECT = "a" * 32
OTHER_PROJECT = "b" * 32
AGENTS = ("product", "developer", "tester", "commander", "other")
HAS_IMAGES = importlib.util.find_spec("PIL") is not None
HAS_PDF = importlib.util.find_spec("pypdfium2") is not None and HAS_IMAGES


class Projects:
    def __init__(self):
        self.projects = {identity: dict(id=identity, members={role: role for role in AGENTS[:4]})
                         for identity in (PROJECT, OTHER_PROJECT)}

    def project(self, identity):
        if identity not in self.projects:
            raise TownError(404, "研发项目不存在")
        return self.projects[identity]


class LocalApp:
    def __init__(self, root):
        self.data_dir = root
        self.lock = threading.RLock()
        self.development = Projects()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.data_dir / "synthetic.sqlite3")
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _agent(self, db, identity):
        if identity not in AGENTS:
            raise TownError(404, "没有找到这位居民")
        return dict(id=identity)


def image_bytes(kind="PNG", size=(160, 100), private=False):
    from PIL import Image, PngImagePlugin
    image = Image.new("RGB", size, (60, 120, 180))
    stream = io.BytesIO()
    options = {}
    if private and kind == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("private-marker", "SYNTHETIC_PRIVATE_METADATA")
        options["pnginfo"] = metadata
    image.save(stream, format=kind, **options)
    image.close()
    return stream.getvalue()


def docx_bytes(body=None, image=None, relationship=None, extra=None, compression=zipfile.ZIP_DEFLATED):
    body = body or ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>合成章节</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>用户可以创建合成任务。</w:t></w:r></w:p>'
                    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>字段</w:t></w:r></w:p></w:tc>'
                    '<w:tc><w:p><w:r><w:t>标题必填</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    if image is not None:
        body += '<w:p><w:r><w:t>原型截图说明</w:t></w:r><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>'
    entries = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>' + body + '</w:body></w:document>',
    }
    if relationship is not None or image is not None:
        entries["word/_rels/document.xml.rels"] = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
            (relationship or '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image.png"/>') + '</Relationships>')
    if image is not None:
        entries["word/media/image.png"] = image
    entries.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def pdf_bytes(pages=1, with_text=True):
    """Small valid PDF, including visible vector shapes on text-layer pages."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    references = []
    for index in range(pages):
        page_number = len(objects) + 1
        references.append(('%d 0 R' % page_number).encode())
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % (page_number + 1)).encode())
        text = ('BT /F1 14 Tf 20 260 Td (Synthetic page %d) Tj ET\n' % (index + 1)).encode() if with_text else b''
        stream = text + b"0.2 0.4 0.8 rg 20 20 180 160 re f\n"
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream")
    objects[1] = b"<< /Type /Pages /Count " + str(pages).encode() + b" /Kids [" + b" ".join(references) + b"] >>"
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(('%d 0 obj\n' % number).encode() + value + b"\nendobj\n")
    start = len(result)
    result.extend(('xref\n0 %d\n0000000000 65535 f \n' % len(offsets)).encode())
    for offset in offsets[1:]:
        result.extend(('%010d 00000 n \n' % offset).encode())
    result.extend(('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(offsets), start)).encode())
    return bytes(result)


class DocumentsStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = LocalApp(self.root)
        self.documents = Documents(self.app)
        network = patch("socket.socket.connect", side_effect=AssertionError("Tests must never access network"))
        network.start()
        self.addCleanup(network.stop)

    def upload(self, content=b"Synthetic local requirement", name="requirement.txt", **scope):
        data = dict(name=name, content_base64=base64.b64encode(content).decode(),
                    agent_id="product", classification="private")
        data.update(scope)
        return self.documents.upload(data)

    def shared(self, content=b"Synthetic shared requirement", name="requirement.txt", **fields):
        data = dict(name=name, content_base64=base64.b64encode(content).decode(),
                    agent_id="product", classification="requirement", project_id=PROJECT)
        data.update(fields)
        return self.documents.upload(data)

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(TownError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return str(caught.exception)

    def test_private_attachment_is_local_immutable_and_visible_only_to_owner(self):
        item = self.upload("合成内容 😀".encode())
        self.assertEqual(item["scope"], dict(classification="private", agent_id="product", project_id=None))
        self.assertEqual(item["fragments"][0]["text"], "合成内容 😀")
        self.assertEqual(self.documents.read(item["id"], "product"), item)
        listed = self.documents.list("product")
        self.assertEqual([entry["id"] for entry in listed], [item["id"]])
        self.assertEqual(listed[0]["fragment_count"], 1)
        self.assertNotIn("text", listed[0]["fragments"][0])
        self.assertEqual(self.documents.list("developer"), [])
        self.assert_error(403, self.documents.read, item["id"], "developer")
        self.assert_error(403, self.documents.resolve, [item["id"]], "developer", endpoint="https://synthetic.invalid")
        self.assertEqual(Documents(self.app).read(item["id"], "product"), item)
        self.assertEqual(self.upload("合成内容 😀".encode())["id"], item["id"])
        with self.app.db() as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE v2_documents SET classification='requirement' WHERE id=?", (item["id"],))
        self.assertNotIn(str(self.root), json.dumps(item))

    def test_project_requirement_is_shared_only_with_current_execution_roles(self):
        item = self.shared(agent_id=None)
        for role in ("product", "developer", "tester"):
            self.assertEqual(self.documents.read(item["id"], role, PROJECT)["id"], item["id"])
        for role, project in (("commander", PROJECT), ("other", PROJECT), ("product", OTHER_PROJECT), ("product", None)):
            self.assert_error(403, self.documents.read, item["id"], role, project)
        self.assertEqual(self.documents.list("commander", PROJECT), [])
        self.app.development.projects[PROJECT]["members"]["developer"] = "other"
        self.assert_error(403, self.documents.read, item["id"], "developer", PROJECT)
        self.assertEqual(self.documents.read(item["id"], "other", PROJECT)["id"], item["id"])

    def test_bound_private_scope_never_becomes_project_shared(self):
        item = self.upload(project_id=PROJECT)
        self.assert_error(403, self.documents.read, item["id"], "product")
        self.assert_error(403, self.documents.read, item["id"], "product", OTHER_PROJECT)
        self.assert_error(403, self.documents.read, item["id"], "developer", PROJECT)
        self.assertEqual(self.documents.read(item["id"], "product", PROJECT)["id"], item["id"])
        self.assert_error(403, self.shared, b"Synthetic local requirement")
        self.assert_error(403, self.upload, b"outside", project_id=PROJECT, agent_id="other")

    def test_upload_accepts_bytes_only_and_rejects_active_types_paths_and_bad_names(self):
        base = dict(name="safe.txt", content_base64="bG9jYWw=", agent_id="product")
        for changes in (dict(path="/private/synthetic"), dict(url="https://synthetic.invalid/document"),
                        dict(name="../escape.txt"), dict(name="a\\escape.txt"), dict(name="x.svg"),
                        dict(name="x.html"), dict(name="x.docm"), dict(content_base64="data:text/plain;base64,bG9jYWw="),
                        dict(content_base64="not base64"), dict(classification="public"), dict(agent_id=None)):
            with self.subTest(changes=changes):
                self.assert_error(400, self.documents.upload, dict(base, **changes))
        self.assertEqual(list(self.documents.root.iterdir()), [])

    def test_text_must_be_utf8_and_active_markup_is_downloaded_as_plain_text(self):
        self.assert_error(400, self.upload, b"\xff\xfeinvalid")
        self.assert_error(400, self.upload, b"text\x00binary")
        item = self.upload(b"<script>synthetic</script>\n# Title", name="notes.md")
        asset = self.documents.asset(item["id"], agent_id="product")
        self.assertEqual(asset["mime"], "text/plain")
        self.assertEqual(asset["disposition"], "attachment")
        self.assertEqual(asset["path"].read_bytes(), b"<script>synthetic</script>\n# Title")

    def test_upload_and_store_bounds_fail_without_partial_files(self):
        with patch.object(documents, "MAX_UPLOAD_BYTES", 12):
            self.assert_error(413, self.upload, b"x" * 13)
            self.upload(b"x" * 12)
        with patch.object(documents, "MAX_DOCUMENTS", 1):
            self.assert_error(413, self.upload, b"second")
        with patch.object(documents, "MAX_STORE_BYTES", 1):
            self.assert_error(413, self.upload, b"third")
        self.assertEqual(len(list(self.documents.root.iterdir())), 1)
        self.assertFalse(list(self.documents.root.glob("*.pending")))

    def test_at_least_sixteen_documents_can_be_stored_but_model_batches_are_bounded(self):
        items = [self.upload(("synthetic item %d" % index).encode()) for index in range(16)]
        self.assertEqual(len(self.documents.list("product")), 16)
        self.assertEqual(len(self.documents.list("product", limit=8)), 8)
        self.assert_error(400, self.documents.list, "product", limit=101)
        self.assert_error(400, self.documents.resolve, [item["id"] for item in items[:5]], "product")
        self.assertEqual(len(self.documents.resolve([item["id"] for item in items[:4]], "product")), 4)
        self.assert_error(400, self.documents.resolve, [items[0]["id"]] * 2, "product")

    def test_original_and_metadata_tampering_are_detected_before_use(self):
        item = self.upload()
        original = self.documents.root / item["id"] / "original.txt"
        original.write_bytes(b"changed")
        self.assert_error(409, self.documents.read, item["id"], "product")
        item = self.upload(b"second document")
        path = self.documents.root / item["id"] / "metadata.json"
        data = json.loads(path.read_text())
        data["scope"]["classification"] = "requirement"
        path.write_text(json.dumps(data))
        self.assert_error(409, self.documents.read, item["id"], "product")

    def test_path_traversal_symlinks_and_hardlinks_never_expose_other_files(self):
        for identity in ("../outside", "a" * 31, "/absolute", "a" * 32 + "/.."):
            self.assert_error(400, self.documents.read, identity, "product")
        item = self.upload()
        original = self.documents.root / item["id"] / "original.txt"
        outside = self.root / "outside.txt"
        outside.write_bytes(b"SYNTHETIC_OUTSIDE_PRIVATE_DATA")
        original.unlink()
        original.symlink_to(outside)
        self.assert_error(503, self.documents.read, item["id"], "product")
        original.unlink()
        os.link(outside, original)
        self.assert_error(503, self.documents.read, item["id"], "product")
        self.assertEqual(outside.read_bytes(), b"SYNTHETIC_OUTSIDE_PRIVATE_DATA")

    def test_symlink_storage_root_is_rejected(self):
        self.documents.root.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.documents.root.symlink_to(outside, target_is_directory=True)
        self.assert_error(503, Documents, self.app)
        self.assertEqual(list(outside.iterdir()), [])

    def test_docx_text_table_cells_and_section_labels_remain_associated(self):
        item = self.upload(docx_bytes(), "requirements.docx")
        text = "\n".join(fragment["text"] for fragment in item["fragments"])
        self.assertIn("用户可以创建合成任务", text)
        self.assertIn("字段 | 标题必填", text)
        self.assertTrue(all(fragment["label"] == "合成章节" for fragment in item["fragments"]))
        self.assertEqual(item["status"], "ready")

    def test_docx_rejects_external_relationships_macros_traversal_entities_and_bombs(self):
        variants = [
            docx_bytes(relationship='<Relationship Id="x" Type="image" Target="https://synthetic.invalid/x" TargetMode="External"/>'),
            docx_bytes(relationship='<Relationship Id="x" Type="image" Target="../../../outside"/>'),
            docx_bytes(extra={"word/vbaProject.bin": b"synthetic macro"}),
            docx_bytes(extra={"../escape": b"synthetic"}),
            docx_bytes(extra={"word/media/active.svg": b"<svg/>"}),
            docx_bytes(extra={"word/document.xml": '<!DOCTYPE document [<!ENTITY x "expanded">]><document>&x;</document>'}),
            docx_bytes(extra={"word/repeated.xml": b"x" * 100000}),
        ]
        for raw in variants:
            with self.subTest(size=len(raw)), self.assertRaises(TownError):
                self.upload(raw, "requirements.docx")
        with patch.object(documents, "MAX_EXPANDED_BYTES", 100):
            self.assert_error(413, self.upload, docx_bytes(), "requirements.docx")
        self.assertEqual(list(self.documents.root.iterdir()), [])

    def test_docx_unparsed_sections_are_explicitly_partial(self):
        item = self.upload(docx_bytes(extra={"word/comments.xml": "<comments/>"}), "requirements.docx")
        self.assertEqual(item["status"], "partial")
        self.assertIn("批注", item["warnings"][0])

    def test_missing_parser_dependencies_fail_clearly_without_creating_receipts(self):
        with patch.object(documents, "_pillow", side_effect=TownError(503, "缺少 Pillow")):
            self.assertIn("Pillow", self.assert_error(503, self.upload, b"synthetic", "image.png"))
        with patch.object(documents, "_pdfium", side_effect=TownError(503, "缺少 pypdfium2")):
            self.assertIn("pypdfium2", self.assert_error(503, self.upload, pdf_bytes(), "requirements.pdf"))
        self.assertEqual(list(self.documents.root.iterdir()), [])

    @unittest.skipUnless(HAS_IMAGES, "Pillow runtime required")
    def test_images_are_decoded_resized_and_metadata_is_removed(self):
        from PIL import Image
        for extension, kind in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")):
            with self.subTest(kind=kind):
                item = self.upload(image_bytes(kind, (2400, 1400), private=True), "screenshot." + extension)
                image = item["images"][0]
                self.assertLessEqual(max(image["width"], image["height"]), 1280)
                resolved = self.documents.resolve([item["id"]], "product")[0]
                raw = base64.b64decode(resolved["images"][0]["content_base64"])
                self.assertNotIn(b"SYNTHETIC_PRIVATE_METADATA", raw)
                with Image.open(io.BytesIO(raw)) as parsed:
                    self.assertEqual(parsed.format, "JPEG")
                    self.assertEqual(dict(parsed.getexif()), {})
                self.assertNotIn(str(self.root), json.dumps(resolved))

    @unittest.skipUnless(HAS_IMAGES, "Pillow runtime required")
    def test_image_type_pixels_and_derivative_hashes_are_enforced(self):
        raw = image_bytes()
        self.assert_error(400, self.upload, raw, "wrong.jpg")
        with patch.object(documents, "MAX_IMAGE_PIXELS", 100):
            self.assert_error(413, self.upload, raw, "too-large.png")
        item = self.upload(raw, "safe.png")
        image = item["images"][0]
        self.assert_error(403, self.documents.asset, item["id"], image["id"], agent_id="developer")
        self.assert_error(400, self.documents.asset, item["id"], "../image", agent_id="product")
        path = self.documents.root / item["id"] / (image["id"] + ".jpg")
        path.write_bytes(b"changed")
        self.assert_error(409, self.documents.resolve, [item["id"]], "product")

    @unittest.skipUnless(HAS_IMAGES, "Pillow runtime required")
    def test_docx_embedded_image_keeps_its_heading_and_nearby_text(self):
        item = self.upload(docx_bytes(image=image_bytes()), "requirements.docx")
        image_fragment = next(fragment for fragment in item["fragments"] if fragment.get("image_id"))
        self.assertEqual(image_fragment["label"], "合成章节")
        self.assertIn("原型截图说明", image_fragment["text"])
        result = self.documents.resolve([item["id"]], "product", fragment_ids={item["id"]: [image_fragment["id"]]})[0]
        self.assertEqual(result["fragments"], [image_fragment])
        self.assertEqual(result["images"][0]["id"], image_fragment["image_id"])
        self.assert_error(400, self.documents.resolve, [item["id"]], "product", fragment_ids={item["id"]: ["unknown"]})

    @unittest.skipUnless(HAS_PDF, "Pillow and pypdfium2 runtime required")
    def test_pdf_text_pages_and_scanned_pages_both_have_page_images(self):
        for with_text in (True, False):
            with self.subTest(with_text=with_text):
                item = self.upload(pdf_bytes(2, with_text), "requirements.pdf")
                self.assertEqual(item["pages"], 2)
                self.assertEqual(len(item["fragments"]), 2)
                self.assertEqual(len(item["images"]), 2)
                self.assertEqual(item["status"], "ready")
                self.assertTrue(all(fragment.get("image_id") for fragment in item["fragments"]))
                self.assertEqual("Synthetic page 1" in item["fragments"][0]["text"], with_text)

    @unittest.skipUnless(HAS_PDF, "Pillow and pypdfium2 runtime required")
    def test_pdf_full_metadata_and_fragment_selection_allow_batches_after_page_eight(self):
        item = self.upload(pdf_bytes(10), "requirements.pdf")
        self.assertEqual(len(self.documents.read(item["id"], "product")["fragments"]), 10)
        first = self.documents.resolve([item["id"]], "product")[0]
        self.assertEqual(len(first["images"]), 8)
        self.assertEqual(len(first["remaining_fragment_ids"]), 2)
        final_fragment = item["fragments"][-1]
        later = self.documents.resolve([item["id"]], "product", fragment_ids={item["id"]: [final_fragment["id"]]})[0]
        self.assertEqual(later["fragments"][0]["label"], "第 10 页")
        self.assertEqual(len(later["images"]), 1)
        self.assertEqual(later["remaining_fragment_ids"], [])

    @unittest.skipUnless(HAS_PDF, "Pillow and pypdfium2 runtime required")
    def test_pdf_eighty_pages_are_available_and_later_pages_are_explicitly_skipped(self):
        item = self.upload(pdf_bytes(81), "requirements.pdf")
        self.assertEqual(item["pages"], 81)
        self.assertEqual(len(item["fragments"]), 80)
        self.assertEqual(len(item["images"]), 80)
        self.assertEqual(item["status"], "partial")
        self.assertTrue(any("第 81 至 81 页已跳过" in warning for warning in item["warnings"]))


if __name__ == "__main__":
    unittest.main()
