"""The helper subprocess protocol, driven against a fake helper.

A fake stands in for Java here so the failure paths that matter -- a timeout while
Shamela is reindexing, a crash mid-request, a stderr trace worth reporting -- can be
provoked deliberately instead of waited for.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from shamela_mcp import errors
from shamela_mcp.bridge import JavaBridge
from shamela_mcp.discover import Library, Runtime

FAKE_HELPER = textwrap.dedent(
    '''
    import json, sys, time

    # The real helper is launched with -Dfile.encoding=UTF-8; match that here so
    # Arabic survives the pipe on a Windows console codepage.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"

    if MODE == "no-ready":
        sys.stderr.write("Exception in thread main: NoClassDefFoundError\\n")
        sys.stderr.flush()
        sys.exit(1)

    print(json.dumps({"id": "ready", "ok": True}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        cmd = request.get("cmd")
        if cmd == "close":
            print(json.dumps({"id": request.get("id"), "ok": True,
                              "result": {"closed": True}}), flush=True)
            break
        if cmd == "hang":
            time.sleep(60)
            continue
        if cmd == "die":
            sys.stderr.write("java.lang.OutOfMemoryError: Java heap space\\n")
            sys.stderr.flush()
            sys.exit(3)
        if cmd == "noise":
            # Non-protocol chatter must be skipped, not parsed.
            print("Picked up JAVA_TOOL_OPTIONS: -Dfoo", flush=True)
            print("not json at all", flush=True)
            print(json.dumps({"id": request.get("id"), "ok": True,
                              "result": {"noise": "survived"}}), flush=True)
            continue
        if cmd == "fail":
            print(json.dumps({"id": request.get("id"), "ok": False,
                              "error": "IOException: index directory not found"}), flush=True)
            continue
        if cmd == "health":
            print(json.dumps({"id": request.get("id"), "ok": True, "result": {
                "java_version": "21.0.10", "lucene_version": "10.4.0",
                "page_docs": 100, "page_generation": 7, "book_field": "book_key"}}), flush=True)
            continue
        if cmd == "get_pages":
            groups = []
            for item in request.get("requests", []):
                groups.append({"book_id": item["bookId"], "results": [
                    {"id": i, "found": i != 999, "body": None if i == 999 else f"body-{i}",
                     "foot": None} for i in item["ids"]]})
            print(json.dumps({"id": request.get("id"), "ok": True,
                              "result": {"groups": groups}}), flush=True)
            continue
        if cmd == "search":
            print(json.dumps({"id": request.get("id"), "ok": True, "result": {
                "total_hits": 3, "total_hits_exact": True,
                "hits": [{"book_id": "42", "page_id": 7, "doc": 1, "score": 1.5}],
                "has_more": False, "echo_store": request.get("storeDir"),
                "echo_groups": request.get("groups")}}), flush=True)
            continue
        print(json.dumps({"id": request.get("id"), "ok": True, "result": {"cmd": cmd}}), flush=True)
    '''
)


@pytest.fixture()
def helper_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE_HELPER, encoding="utf-8")
    return script


def make_bridge(tmp_path: Path, helper: Path, mode: str = "normal", **kwargs) -> JavaBridge:
    database = tmp_path / "database"
    (database / "store").mkdir(parents=True, exist_ok=True)
    (database / "service").mkdir(exist_ok=True)
    (database / "book").mkdir(exist_ok=True)
    (tmp_path / "app").mkdir(exist_ok=True)
    (database / "master.db").touch()
    library = Library(
        root=tmp_path,
        database_dir=database,
        app_dir=tmp_path / "app",
        master_db=database / "master.db",
        store_dir=database / "store",
        book_dir=database / "book",
        service_dir=database / "service",
        source="argument",
    )
    lucene = tmp_path / "lucene"
    lucene.mkdir(exist_ok=True)
    (lucene / "lucene-core.jar").touch()
    runtime = Runtime(java_path=Path(sys.executable), lucene_dir=lucene, problems_ar=())

    class FakeHelperBridge(JavaBridge):
        """Same lifecycle and framing; a Python fake stands in for the JVM."""

        def launch_command(self) -> list[str]:
            self._preflight()
            return [sys.executable, str(helper), mode]

    return FakeHelperBridge(library, runtime, jar_path=helper, **kwargs)


class TestHappyPath:
    def test_health_round_trip(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            info = bridge.health()
            assert info["java_version"] == "21.0.10"
            assert info["book_field"] == "book_key"
        finally:
            bridge.close()

    def test_store_dir_travels_on_every_request(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            result = bridge.search(field="body", mode="all_terms", groups=[["أ"]])
            assert result["echo_store"] == str(bridge.library.store_dir)
            assert result["echo_groups"] == [["أ"]]
        finally:
            bridge.close()

    def test_get_pages_indexes_by_book_and_page(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            pages = bridge.get_pages([(42, [1, 2, 999])])
            assert pages[42][1]["body"] == "body-1"
            # A miss is reported, never silently dropped.
            assert pages[42][999]["found"] is False
        finally:
            bridge.close()

    def test_empty_fetch_skips_the_round_trip(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            assert bridge.get_pages([]) == {}
            assert bridge.get_pages([(42, [])]) == {}
        finally:
            bridge.close()

    def test_non_protocol_output_is_ignored(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            # A JVM warning on stdout must not be mistaken for a response.
            assert bridge.call("noise")["noise"] == "survived"
        finally:
            bridge.close()

    def test_requests_are_serialised(self, tmp_path: Path, helper_script: Path) -> None:
        import threading

        bridge = make_bridge(tmp_path, helper_script)
        results: list[str] = []

        def worker() -> None:
            results.append(bridge.health()["java_version"])

        try:
            threads = [threading.Thread(target=worker) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert results == ["21.0.10"] * 5
        finally:
            bridge.close()


class TestFailures:
    def test_timeout_is_reported_as_a_busy_index(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script, timeout_ms=1500)
        try:
            with pytest.raises(errors.ShamelaError) as caught:
                bridge.call("hang")
            assert caught.value.code == errors.INDEX_BUSY
            # The scholar-facing message must name the likely cause.
            assert "الفهرسة" in caught.value.message_ar or "ينزّل" in caught.value.message_ar
        finally:
            bridge.close()

    def test_helper_error_becomes_engine_unavailable(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            with pytest.raises(errors.ShamelaError) as caught:
                bridge.call("fail")
            assert caught.value.code == errors.ENGINE_UNAVAILABLE
            assert "index directory not found" in caught.value.detail_en
        finally:
            bridge.close()

    def test_crash_reports_the_stderr_tail(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            with pytest.raises(errors.ShamelaError) as caught:
                bridge.call("die")
            assert caught.value.code == errors.ENGINE_UNAVAILABLE
            # A JVM stack trace is the single most useful diagnostic; it must survive.
            assert any(
                "OutOfMemoryError" in line for line in caught.value.data.get("stderr", [])
            )
        finally:
            bridge.close()

    def test_startup_failure_is_explained(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script, mode="no-ready")
        with pytest.raises(errors.ShamelaError) as caught:
            bridge.health()
        assert caught.value.code == errors.ENGINE_UNAVAILABLE
        assert "NoClassDefFoundError" in str(caught.value.data.get("stderr", []))

    def test_respawn_after_a_crash(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        try:
            with pytest.raises(errors.ShamelaError):
                bridge.call("die")
            # The next call must transparently start a fresh helper.
            assert bridge.health()["java_version"] == "21.0.10"
        finally:
            bridge.close()

    def test_missing_java_is_refused_before_spawning(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        bridge.runtime = Runtime(java_path=None, lucene_dir=None, problems_ar=("جافا مفقودة",))
        with pytest.raises(errors.ShamelaError) as caught:
            bridge.health()
        assert caught.value.code == errors.ENGINE_UNAVAILABLE
        assert "جافا مفقودة" in str(caught.value.data)

    def test_launch_command_carries_the_parent_pid_and_classpath(
        self, tmp_path: Path, helper_script: Path
    ) -> None:
        import os

        bridge = make_bridge(tmp_path, helper_script)
        command = JavaBridge.launch_command(bridge)
        assert command[-1] == str(os.getpid())
        assert command[-2] == "dev.shamela.mcp.Main"
        classpath = command[command.index("-cp") + 1]
        # Shamela's own jars, plus our helper.
        assert str(bridge.runtime.lucene_dir) in classpath
        assert str(helper_script) in classpath

    def test_close_is_idempotent(self, tmp_path: Path, helper_script: Path) -> None:
        bridge = make_bridge(tmp_path, helper_script)
        bridge.health()
        bridge.close()
        bridge.close()
        assert bridge._process is None
