"""The Java helper subprocess: lifecycle, framing, and failure reporting.

One helper process serves the whole session. Opening the page index costs seconds --
it is multiple gigabytes -- so paying that once and keeping the process warm is the
difference between a usable tool and an unusable one. The process is started lazily,
shut down when idle, and restarted transparently on the next call.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from . import errors
from .discover import Library, Runtime

log = logging.getLogger(__name__)

HELPER_JAR = Path(__file__).resolve().parent.parent / "java" / "shamela-mcp-helper.jar"

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _shamela_is_running() -> bool:
    """Best-effort check for a running Shamela app, to sharpen slow-read diagnoses."""
    if sys.platform != "win32":
        return False
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq shamela.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "shamela.exe" in proc.stdout.lower()


class JavaBridge:
    """NDJSON client for the helper. Thread-safe; one request in flight at a time."""

    def __init__(
        self,
        library: Library,
        runtime: Runtime,
        timeout_ms: int = 120_000,
        idle_ms: int = 300_000,
        jar_path: Path | None = None,
    ) -> None:
        self.library = library
        self.runtime = runtime
        self.timeout_s = max(1.0, timeout_ms / 1000)
        self.idle_s = max(30.0, idle_ms / 1000)
        self.jar_path = jar_path or HELPER_JAR

        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._last_use = time.monotonic()
        self._idle_timer: threading.Timer | None = None

    # ---------- lifecycle ----------

    def _preflight(self) -> None:
        if self.runtime.java_path is None:
            raise errors.engine_unavailable(
                "bundled java not found",
                {"problems_ar": list(self.runtime.problems_ar)},
            )
        if self.runtime.lucene_dir is None:
            raise errors.engine_unavailable(
                "lucene jar directory not found",
                {"problems_ar": list(self.runtime.problems_ar)},
            )
        if not self.jar_path.is_file():
            raise errors.engine_unavailable(
                f"helper jar missing at {self.jar_path}",
                {"jar_path": str(self.jar_path)},
            )

    def _classpath(self) -> str:
        assert self.runtime.lucene_dir is not None
        # The JVM expands the wildcard itself, so jar names need not be known here.
        return os.pathsep.join([str(self.runtime.lucene_dir / "*"), str(self.jar_path)])

    def launch_command(self) -> list[str]:
        """The command that starts the helper. Overridden in tests."""
        self._preflight()
        assert self.runtime.java_path is not None
        return [
            str(self.runtime.java_path),
            "-Xmx512m",
            # Lucene query rewriting can recurse deeply on large boolean queries.
            "-Xss4m",
            "-Dfile.encoding=UTF-8",
            "-Dstdout.encoding=UTF-8",
            "-Dstderr.encoding=UTF-8",
            "-cp",
            self._classpath(),
            "dev.shamela.mcp.Main",
            # The helper exits when this process does, so a killed Claude Desktop
            # cannot leave a JVM holding the index files open.
            str(os.getpid()),
        ]

    def _spawn(self) -> subprocess.Popen[str]:
        command = self.launch_command()
        env = dict(os.environ)
        # An inherited agent or proxy setting here would corrupt the protocol stream.
        env["JAVA_TOOL_OPTIONS"] = ""

        log.debug("spawning helper: %s", command)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise errors.engine_unavailable(
                f"failed to start the helper: {exc}",
                {"command": command[0]},
            ) from exc

        self._stderr_tail.clear()
        threading.Thread(
            target=self._drain_stderr, args=(process,), name="helper-stderr", daemon=True
        ).start()

        # The helper announces itself before serving, which separates "still starting"
        # from "died on startup" without a probe request.
        assert process.stdout is not None
        ready = process.stdout.readline()
        if not ready.strip():
            detail = "\n".join(self._stderr_tail) or "helper exited before signalling ready"
            process.kill()
            raise errors.engine_unavailable(
                f"helper did not start: {detail}", {"stderr": list(self._stderr_tail)}
            )
        return process

    def _drain_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            text = line.rstrip()
            if text:
                self._stderr_tail.append(text)
                log.debug("helper stderr: %s", text)

    def _ensure_running(self) -> subprocess.Popen[str]:
        if self._process is None or self._process.poll() is not None:
            self._process = self._spawn()
        return self._process

    def close(self) -> None:
        """Shut the helper down; the next call starts a fresh one."""
        with self._lock:
            self._cancel_idle_timer()
            process = self._process
            self._process = None
            if process is None or process.poll() is not None:
                return
            try:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.write('{"id":"bye","cmd":"close"}\n')
                    process.stdin.flush()
                process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            finally:
                if process.poll() is None:
                    process.kill()

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle_shutdown(self) -> None:
        self._cancel_idle_timer()
        timer = threading.Timer(self.idle_s, self._idle_check)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _idle_check(self) -> None:
        with self._lock:
            if self._process is None:
                return
            if time.monotonic() - self._last_use >= self.idle_s:
                log.debug("helper idle; shutting down")
                self.close()
            else:
                self._schedule_idle_shutdown()

    # ---------- request/response ----------

    def call(self, cmd: str, **payload: Any) -> dict[str, Any]:
        with self._lock:
            process = self._ensure_running()
            self._next_id += 1
            request_id = self._next_id
            request = {
                "id": request_id,
                "cmd": cmd,
                "storeDir": str(self.library.store_dir),
                **payload,
            }
            line = json.dumps(request, ensure_ascii=False)

            assert process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.close()
                raise errors.engine_unavailable(
                    f"helper pipe closed while sending {cmd}: {exc}",
                    {"stderr": list(self._stderr_tail)},
                ) from exc

            response = self._read_response(process, request_id, cmd)
            self._last_use = time.monotonic()
            self._schedule_idle_shutdown()
            return response

    def _read_response(
        self, process: subprocess.Popen[str], request_id: int, cmd: str
    ) -> dict[str, Any]:
        assert process.stdout is not None
        result: dict[str, Any] = {}
        failure: BaseException | None = None
        done = threading.Event()

        def reader() -> None:
            nonlocal result, failure
            try:
                while True:
                    line = process.stdout.readline()  # type: ignore[union-attr]
                    if not line:
                        failure = errors.engine_unavailable(
                            f"helper exited during {cmd}",
                            {"stderr": list(self._stderr_tail)},
                        )
                        return
                    text = line.strip()
                    if not text.startswith("{"):
                        continue  # ignore anything that is not a protocol frame
                    try:
                        message = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if message.get("id") != request_id:
                        continue  # a stale frame from an abandoned request
                    result = message
                    return
            except (OSError, ValueError) as exc:  # pragma: no cover - pipe teardown
                failure = errors.engine_unavailable(
                    f"helper read failed during {cmd}: {exc}",
                    {"stderr": list(self._stderr_tail)},
                )
            finally:
                done.set()

        thread = threading.Thread(target=reader, name="helper-read", daemon=True)
        thread.start()

        if not done.wait(self.timeout_s):
            # A read this slow almost always means Shamela is rebuilding its indexes;
            # the helper is unusable until that finishes, so start clean next time.
            self.close()
            raise errors.index_busy(
                f"timed out after {self.timeout_s:.0f}s on {cmd}",
                shamela_running=_shamela_is_running(),
            )

        if failure is not None:
            self.close()
            raise failure

        if not result.get("ok"):
            detail = str(result.get("error", "unknown helper error"))
            raise errors.engine_unavailable(
                f"{cmd} failed: {detail}", {"stderr": list(self._stderr_tail)}
            )
        return result.get("result") or {}

    # ---------- typed helpers ----------

    def health(self) -> dict[str, Any]:
        return self.call("health")

    def search(
        self,
        *,
        field: str,
        mode: str,
        groups: list[list[str]],
        book_ids: list[str] | None = None,
        limit: int = 10,
        after_doc: int | None = None,
        after_score: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "field": field,
            "mode": mode,
            "groups": groups,
            "limit": limit,
        }
        if book_ids:
            payload["bookIds"] = book_ids
        if after_doc is not None:
            payload["afterDoc"] = after_doc
            payload["afterScore"] = after_score if after_score is not None else 0.0
        return self.call("search", **payload)

    def count_by_book(
        self, *, field: str, mode: str, groups: list[list[str]], book_ids: list[str]
    ) -> dict[str, Any]:
        return self.call(
            "count_by_book", field=field, mode=mode, groups=groups, bookIds=book_ids
        )

    def get_pages(self, requests: list[tuple[int, list[int]]]) -> dict[int, dict[int, dict]]:
        """Fetch page bodies. Returns ``{book_id: {page_id: {body, foot, found}}}``."""
        return self._fetch("get_pages", requests)

    def get_titles(self, requests: list[tuple[int, list[int]]]) -> dict[int, dict[int, dict]]:
        """Fetch heading texts. Returns ``{book_id: {title_id: {body, parent, found}}}``."""
        return self._fetch("get_titles", requests)

    def _fetch(
        self, cmd: str, requests: list[tuple[int, list[int]]]
    ) -> dict[int, dict[int, dict]]:
        payload = [
            {"bookId": str(book_id), "ids": list(ids)} for book_id, ids in requests if ids
        ]
        if not payload:
            return {}
        raw = self.call(cmd, requests=payload)
        out: dict[int, dict[int, dict]] = {}
        for group in raw.get("groups", []):
            try:
                book_id = int(group.get("book_id"))
            except (TypeError, ValueError):
                continue
            rows = {}
            for row in group.get("results", []):
                rows[int(row["id"])] = row
            out[book_id] = rows
        return out

    def probe(self, *, index: str, field: str, terms: list[str]) -> dict[str, Any]:
        return self.call("probe", index=index, field=field, terms=terms)
