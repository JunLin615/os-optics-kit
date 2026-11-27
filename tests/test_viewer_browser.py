"""Real-browser live-viewer regressions (Playwright + Chromium).

Verifies under the shipped strict Content-Security-Policy (no ``unsafe-eval``):
1. A delayed valid scene B arriving after a newer on-disk integrity failure commits coherent B
   WITHOUT clearing the newer ``RUN_INTEGRITY_FAILED`` waiting warning, and clears the warning
   only once valid input returns.
2. A slow-but-valid scene load (16.5 s) is NOT restarted on an arbitrary timer and finishes
   cleanly in one request.
3. Under continuous rapid writes (every 0.45 s) with a 1.1 s scene load, a newly opened page
   still renders a coherent first frame *during* the burst and converges to the final revision.
4. Navigating to ``/engine-view/?rev=1`` redirects to the canonical snapshot-bound URL and
   renders rev-1's actual 2-beam WebGL document even after rev-2 (1 beam) is published.
5. An iframe whose engine script is held on a barrier until its snapshot is evicted (cold and warm
   start) does not stall the page: after release and a final valid B, scene and ledger reach B.
6. A frame whose engine script or page fails to load (HTTP 503) releases the in-flight slot and is
   retried, so the page reaches the valid latest run without a refresh.
7. A viewer restarted on the same port (state numbering from 1 again) is followed by the open page,
   scene and ledger together, instead of being ignored as older than the previous process.
8. A failed ``/api/snapshot`` read (HTTP 503) is retried on its own: once the API answers again the page
   shows the latest run, clears the read error and is live again, with no refresh and no further write.

Missing optional dependencies skip these checks locally unless
``OS_OPTICS_KIT_REQUIRE_VIEWER_BROWSER=1`` is set (as in CI); ordinary users of
``os-optics-kit view`` never need Playwright.
"""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

import pytest

from os_optics_kit import render_views as render
from os_optics_kit.viewer import LocalViewerServer, ViewerSession, resolve_target

ROOT = Path(__file__).resolve().parents[1]
DETECTOR_CASE_PROJECT = ROOT / "cases" / "detector-measurements" / "project.json"
REQUIRE_BROWSER = os.environ.get("OS_OPTICS_KIT_REQUIRE_VIEWER_BROWSER") == "1"


class _DelayingProxy:
    """Thread-per-request loopback HTTP/SSE proxy that injects delays without blocking Playwright."""

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        on_request: Callable[[str], tuple[int, bytes] | None] | None = None,
    ) -> None:
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.on_request = on_request
        self._stop = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def __enter__(self) -> _DelayingProxy:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                self._forward("GET")

            def do_HEAD(self) -> None:  # noqa: N802
                self._forward("HEAD")

            def _forward(self, method: str) -> None:
                if proxy.on_request is not None:
                    override = proxy.on_request(self.path)
                    if override is not None:
                        # The hook answers this request itself (an injected failure), without forwarding it.
                        status, body = override
                        self.send_response(status)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        if method != "HEAD":
                            self.wfile.write(body)
                        self.wfile.flush()
                        return
                conn =http.client.HTTPConnection(proxy.upstream_host, proxy.upstream_port, timeout=30)
                try:
                    fwd_headers = {
                        k: v
                        for k, v in self.headers.items()
                        if k.lower() not in ("host", "connection", "accept-encoding")
                    }
                    fwd_headers["Host"] = f"{proxy.upstream_host}:{proxy.upstream_port}"
                    fwd_headers["Connection"] = "close"
                    conn.request(method, self.path, headers=fwd_headers)
                    resp = conn.getresponse()
                    ctype = resp.getheader("Content-Type", "")
                    is_sse = ctype.lower().startswith("text/event-stream")

                    self.send_response(resp.status, resp.reason)
                    for k, v in resp.getheaders():
                        if k.lower() in ("transfer-encoding", "connection"):
                            continue
                        self.send_header(k, v)
                    self.send_header("Connection", "close")
                    self.end_headers()

                    if method == "HEAD":
                        return
                    if is_sse:
                        while not proxy._stop.is_set():
                            line = resp.fp.readline()
                            if not line:
                                break
                            self.wfile.write(line)
                            self.wfile.flush()
                    else:
                        body = resp.read()
                        self.wfile.write(body)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    return
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

        class _Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._httpd = _Server(("127.0.0.1", 0), Handler)
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3.0)


def _fixture_a() -> dict:
    """Fixture A: 1 mW, completed, 2 traced branches."""
    doc = json.loads(DETECTOR_CASE_PROJECT.read_text(encoding="utf-8"))
    for comp in doc["components"]:
        if comp["instance_id"] == "SRC":
            comp["source_model"]["power_w"] = 0.001
    for obs in doc["observations"]:
        if obs["observation_id"] == "reference":
            obs["absorbing"] = False
        if obs["observation_id"] == "signal":
            obs["aperture"]["radius_mm"] = 0.4
    return doc


def _fixture_b() -> dict:
    """Fixture B: 3 mW, scientifically PARTIAL (max_paths=1), 1 traced branch."""
    doc = json.loads(DETECTOR_CASE_PROJECT.read_text(encoding="utf-8"))
    for comp in doc["components"]:
        if comp["instance_id"] == "SRC":
            comp["source_model"]["power_w"] = 0.003
    doc["compute_budget"] = {
        "max_paths": 1,
        "max_trace_steps": 2048,
        "max_interactions_per_path": 64,
        "max_interference_pairs": 256,
    }
    return doc


def _replace_dir_from_subprocess(src_dir: Path, dst_dir: Path) -> None:
    # On Windows a directory cannot be renamed while the watcher has one of its files open, so the swap is retried
    # briefly; it is an atomic replacement either way, and nothing a test asserts depends on which attempt succeeds.
    code = """
from pathlib import Path
import shutil
import sys
import time


def swap(a, b):
    for attempt in range(100):
        try:
            return a.rename(b)
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.02)


src, dst = Path(sys.argv[1]), Path(sys.argv[2])
staging = dst.parent / (dst.name + ".staging")
shutil.rmtree(staging, ignore_errors=True)
shutil.copytree(src, staging)
old = dst.parent / (dst.name + ".old")
shutil.rmtree(old, ignore_errors=True)
if dst.exists():
    swap(dst, old)
swap(staging, dst)
shutil.rmtree(old, ignore_errors=True)
"""
    done =subprocess.run([sys.executable, "-c", code, str(src_dir), str(dst_dir)], capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise RuntimeError(f"subprocess replace failed: {done.stderr}")


def _copy_file_from_subprocess(src: Path, dst: Path) -> None:
    code = "from pathlib import Path; import shutil, sys; shutil.copy2(Path(sys.argv[1]), Path(sys.argv[2]))"
    done = subprocess.run([sys.executable, "-c", code, str(src), str(dst)], capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise RuntimeError(f"subprocess copy failed: {done.stderr}")


def _observe_visible_state(page) -> dict:
    """Inspect BOTH the visible host panel AND the active visible WebGL 3D iframe together.

    Uses Playwright's CDP function invocation (no ``eval`` / ``new Function``), safe under strict CSP.
    """
    return page.evaluate(
        """() => {
            const app = document.getElementById('app');
            const activeFrame = document.querySelector('iframe.engine-frame.active');
            const errBanner = document.getElementById('banner-error');
            const powerText = (document.getElementById('power-body')?.textContent || '').trim();
            const sceneMeta = (document.getElementById('scene-meta')?.textContent || '').trim();
            const revText = (document.getElementById('badge-rev')?.textContent || '').trim();
            let sceneInfo = null;
            try {
                const win = activeFrame && activeFrame.contentWindow;
                const v = win && win.__opensciViewer;
                if (v && v.doc) {
                    sceneInfo = {
                        project_hash: v.doc.identity?.project_hash || null,
                        scene_content_hash: v.doc.identity?.scene_content_hash || null,
                        beam_count: Array.isArray(v.doc.beams) ? v.doc.beams.length : 0,
                        part_count: Array.isArray(v.doc.parts) ? v.doc.parts.length : 0,
                        rendered_beam_objects: Array.isArray(v.beamObjects) ? v.beamObjects.length : 0,
                        frame_snapshot_id: activeFrame.getAttribute('data-snapshot-id') || '',
                        frame_src: activeFrame.getAttribute('src') || '',
                    };
                }
            } catch (e) {
                sceneInfo = { error: String(e) };
            }
            return {
                app_revision: app?.getAttribute('data-revision') || '',
                app_snapshot_id: app?.getAttribute('data-snapshot-id') || '',
                app_status: app?.getAttribute('data-status') || '',
                app_state_seq: app?.getAttribute('data-state-seq') || '',
                rev_text: revText,
                power_text: powerText,
                scene_meta: sceneMeta,
                banner_visible: !!(errBanner && !errBanner.classList.contains('hidden')),
                banner_text: (errBanner?.textContent || '').trim(),
                scene: sceneInfo,
            };
        }"""
    )


def _assert_coherent_a_or_b(obs: dict, *, hash_a: str, hash_b: str) -> None:
    """Assert that the visible panel and active 3D WebGL scene ALWAYS agree on either A or B."""
    scene = obs["scene"]
    assert scene is not None and "error" not in scene, f"active 3D scene unavailable: {obs}"
    assert obs["app_snapshot_id"] == scene["frame_snapshot_id"], f"panel/iframe snapshot mismatch: {obs}"

    if scene["project_hash"] == hash_a:
        assert scene["beam_count"] == 2, f"A scene must have 2 beams: {obs}"
        assert "1 mW" in obs["power_text"], f"A scene shown beside non-A power panel: {obs}"
        assert "3 mW" not in obs["power_text"], f"B power leaked onto A scene: {obs}"
        assert "2 branches" in obs["scene_meta"], f"A scene meta mismatch: {obs}"
    elif scene["project_hash"] == hash_b:
        assert scene["beam_count"] == 1, f"B scene must have 1 beam: {obs}"
        assert "3 mW" in obs["power_text"], f"B scene shown beside non-B power panel: {obs}"
        assert "1 mW" not in obs["power_text"], f"A power leaked onto B scene: {obs}"
        assert "1 branches" in obs["scene_meta"], f"B scene meta mismatch: {obs}"
    else:
        raise AssertionError(f"unexpected project_hash in visible scene: {obs}")


@pytest.fixture(scope="module")
def browser_env(tmp_path_factory):
    if REQUIRE_BROWSER:
        import os_optics  # noqa: F401
        import playwright.sync_api  # noqa: F401
    else:
        pytest.importorskip("os_optics")
        pytest.importorskip("playwright.sync_api")

    browser_path = render.find_browser(None)
    if not browser_path:
        if REQUIRE_BROWSER:
            pytest.fail("Chrome/Chromium is required when OS_OPTICS_KIT_REQUIRE_VIEWER_BROWSER=1")
        pytest.skip("Chrome/Chromium is not installed")

    from os_optics.local import run_optical_project
    from os_optics.results import load_run

    base = tmp_path_factory.mktemp("viewer-browser")
    run_a = base / "run_a"
    run_b = base / "run_b"
    run_optical_project(_fixture_a(), output_dir=run_a)
    run_optical_project(_fixture_b(), output_dir=run_b)
    hash_a = load_run(run_a, verify=True).identity()["project_hash"]
    hash_b = load_run(run_b, verify=True).identity()["project_hash"]
    assert hash_a != hash_b
    return browser_path, run_a, run_b, hash_a, hash_b


def test_delayed_valid_scene_preserves_newer_waiting_warning_and_recovers(browser_env, tmp_path):
    """Counterexample 1.1: delayed valid scene B must not clear a newer RUN_INTEGRITY_FAILED warning."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / "watched_run"
    shutil.copytree(run_a, watched)

    b_scene_started = threading.Event()
    release_b_scene = threading.Event()

    def on_request(path: str) -> None:
        if "/engine-view/rev-2-" in path and path.endswith("/view.json"):
            b_scene_started.set()
            release_b_scene.wait(timeout=20.0)

    with ViewerSession(resolve_target(watched), poll_interval=0.08) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with _DelayingProxy(server.host, server.port, on_request=on_request) as proxy:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 840})
                        console_errors: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                        page.goto(f"{proxy.url}?lang=en", wait_until="domcontentloaded")
                        expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)
                        expect(page.locator("#app")).to_have_attribute("data-status", "ok", timeout=10000)
                        obs_a = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_a, hash_a=hash_a, hash_b=hash_b)
                        assert obs_a["scene"]["project_hash"] == hash_a

                        # 1) Replace A with valid B; pause B's scene response while staging.
                        _replace_dir_from_subprocess(run_b, watched)
                        assert b_scene_started.wait(timeout=15.0), "rev-2 view.json was never requested"

                        # While B is still staging, visible UI MUST remain coherent on A (never mixed).
                        obs_mid = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_mid, hash_a=hash_a, hash_b=hash_b)
                        assert obs_mid["scene"]["project_hash"] == hash_a

                        # 2) Before B finishes staging, corrupt B by replacing ray_data.json with A's.
                        _copy_file_from_subprocess(run_a / "ray_data.json", watched / "ray_data.json")
                        expect(page.locator("#app")).to_have_attribute("data-status", "waiting", timeout=15000)
                        expect(page.locator("#banner-error")).to_be_visible(timeout=15000)
                        expect(page.locator("#banner-error")).to_contain_text("RUN_INTEGRITY_FAILED", timeout=15000)

                        # 3) Release valid B's delayed scene response -> B commits atomically,
                        #    AND the newer RUN_INTEGRITY_FAILED waiting warning MUST SURVIVE the swap!
                        release_b_scene.set()
                        expect(page.locator("#app")).to_have_attribute("data-revision", "2", timeout=20000)
                        obs_b_warn = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_b_warn, hash_a=hash_a, hash_b=hash_b)
                        assert obs_b_warn["scene"]["project_hash"] == hash_b
                        assert obs_b_warn["app_status"] == "waiting", f"delayed B cleared waiting status: {obs_b_warn}"
                        assert obs_b_warn["banner_visible"], f"delayed B cleared waiting banner: {obs_b_warn}"
                        assert "RUN_INTEGRITY_FAILED" in obs_b_warn["banner_text"]

                        # 4) Restore clean A -> waiting warning clears ONLY after valid input returns.
                        _replace_dir_from_subprocess(run_a, watched)
                        expect(page.locator("#app")).to_have_attribute("data-revision", "3", timeout=20000)
                        expect(page.locator("#app")).to_have_attribute("data-status", "ok", timeout=15000)
                        expect(page.locator("#banner-error")).to_be_hidden(timeout=15000)

                        obs_rec = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_rec, hash_a=hash_a, hash_b=hash_b)
                        assert obs_rec["scene"]["project_hash"] == hash_a
                        assert console_errors == [], f"unexpected browser console errors: {console_errors}"
                    finally:
                        release_b_scene.set()
                        browser.close()


def test_slow_valid_scene_does_not_restart_and_completes(browser_env, tmp_path):
    """Counterexample 1.2a: a stable valid scene delayed 16.5 s must finish without restarting."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / "watched_slow"
    shutil.copytree(run_a, watched)

    rev2_requests: list[str] = []
    lock = threading.Lock()

    def on_request(path: str) -> None:
        if "/engine-view/rev-2-" in path and path.endswith("/view.json"):
            with lock:
                rev2_requests.append(path)
            time.sleep(16.5)

    with ViewerSession(resolve_target(watched), poll_interval=0.1) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with _DelayingProxy(server.host, server.port, on_request=on_request) as proxy:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 840})
                        console_errors: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                        page.goto(f"{proxy.url}?lang=en", wait_until="domcontentloaded")
                        expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)

                        _replace_dir_from_subprocess(run_b, watched)
                        expect(page.locator("#app")).to_have_attribute("data-revision", "2", timeout=35000)
                        obs_b = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_b, hash_a=hash_a, hash_b=hash_b)
                        assert obs_b["scene"]["project_hash"] == hash_b
                        with lock:
                            assert len(rev2_requests) == 1, f"slow valid scene was restarted: {rev2_requests}"
                        assert console_errors == [], f"unexpected browser console errors: {console_errors}"
                    finally:
                        browser.close()


def test_continuous_burst_shows_first_frame_during_burst_and_converges(browser_env, tmp_path):
    """Counterexample 1.2b: rapid writes (0.45 s) with slower scene loads (1.1 s) must not starve first frame."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / "watched_burst"
    shutil.copytree(run_a, watched)

    def on_request(path: str) -> None:
        if path.startswith("/engine-view/") and path.endswith("/view.json"):
            time.sleep(1.1)

    with ViewerSession(resolve_target(watched), poll_interval=0.08) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with _DelayingProxy(server.host, server.port, on_request=on_request) as proxy:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 840})
                        console_errors: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                        writer_done = threading.Event()
                        writer_errors: list[Exception] = []

                        def burst_writer():
                            try:
                                # 7 alternating replacements every 0.5 s (~3.5 s total), ending on Fixture B.
                                for idx in range(7):
                                    time.sleep(0.5)
                                    src = run_b if (idx % 2 == 0) else run_a
                                    _replace_dir_from_subprocess(src, watched)
                            except Exception as exc:  # noqa: BLE001
                                writer_errors.append(exc)
                            finally:
                                writer_done.set()

                        writer_thread = threading.Thread(target=burst_writer, daemon=True)
                        writer_thread.start()

                        page.goto(f"{proxy.url}?lang=en", wait_until="domcontentloaded")

                        # Verify a coherent first frame appears *during* the active burst (before writer_done!).
                        saw_frame_during_burst = False
                        deadline = time.monotonic() + 15.0
                        while time.monotonic() < deadline and not writer_done.is_set():
                            rev_attr = page.locator("#app").get_attribute("data-revision") or "0"
                            if int(rev_attr) >= 1:
                                obs = _observe_visible_state(page)
                                _assert_coherent_a_or_b(obs, hash_a=hash_a, hash_b=hash_b)
                                saw_frame_during_burst = True
                                break
                            time.sleep(0.1)

                        writer_thread.join(timeout=20.0)
                        assert not writer_errors, f"burst writer failed: {writer_errors}"
                        assert saw_frame_during_burst, "newly opened page starved and displayed no first frame during burst"

                        # Wait for server to settle on final revision and verify browser converges to exact latest rev (B).
                        time.sleep(0.35)
                        final_rev = session.state.envelope()["revision"]
                        expect(page.locator("#app")).to_have_attribute("data-revision", str(final_rev), timeout=20000)
                        obs_final = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_final, hash_a=hash_a, hash_b=hash_b)
                        assert obs_final["scene"]["project_hash"] == hash_b
                        assert console_errors == [], f"unexpected browser console errors: {console_errors}"
                    finally:
                        browser.close()


def test_canonical_engine_view_redirect_renders_exact_requested_revision(browser_env, tmp_path):
    """Counterexample 1.3: /engine-view/?rev=1 redirects to canonical snapshot URL and draws rev 1 after rev 2 exists."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / "watched_redirect"
    shutil.copytree(run_a, watched)

    with ViewerSession(resolve_target(watched), poll_interval=0.08) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            snap_a_id = session.state.envelope()["snapshot"]["snapshot_id"]
            # Advance disk to B (rev 2) BEFORE opening /engine-view/?rev=1!
            _replace_dir_from_subprocess(run_b, watched)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and session.state.envelope()["revision"] < 2:
                time.sleep(0.05)
            assert session.state.envelope()["revision"] >= 2

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                try:
                    page = browser.new_page(viewport={"width": 960, "height": 640})
                    page.goto(f"{server.url}engine-view/?rev=1", wait_until="domcontentloaded")
                    assert page.url.endswith(f"/engine-view/{snap_a_id}/"), f"expected canonical redirect, got {page.url}"
                    expect(page.locator("#footer")).to_contain_text(hash_a[:12], timeout=15000)
                    doc_info = page.evaluate(
                        """() => ({
                            project_hash: window.__opensciViewer?.doc?.identity?.project_hash || null,
                            beam_count: window.__opensciViewer?.doc?.beams?.length || 0,
                        })"""
                    )
                    assert doc_info["project_hash"] == hash_a
                    assert doc_info["beam_count"] == 2
                finally:
                    browser.close()


def _is_engine_bootstrap_script(path: str) -> bool:
    """The engine viewer script an engine-view iframe loads (never the host page's own /assets/viewer.js)."""
    bare = path.split("?", 1)[0]
    return bare.startswith("/engine-") and bare.endswith("/viewer.js")


def _wait_for_revision(session: ViewerSession, revision: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.state.envelope()["revision"] >= revision and session.state.event_payload()["status"] == "ok":
            return
        time.sleep(0.02)
    raise AssertionError(f"watcher never published revision {revision}: {session.state.event_payload()}")


@pytest.mark.parametrize("warm", [False, True], ids=["cold-start", "warm-start"])
def test_bootstrap_script_delayed_past_cache_eviction_still_reaches_latest(browser_env, tmp_path, warm):
    """An iframe whose engine script is held until its snapshot is evicted must not stall the page.

    The engine script request is held on a barrier while an external process publishes more valid runs than the
    snapshot cache holds; it is released, a final valid B is published, and writing stops. Without a refresh, the
    active WebGL scene and the visible power ledger must both reach B.
    """
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / ("watched_warm" if warm else "watched_cold")
    shutil.copytree(run_a, watched)

    gate_armed = threading.Event()
    gate_reached = threading.Event()
    release_gate = threading.Event()
    gated_paths: list[str] = []

    if not warm:
        gate_armed.set()

    def on_request(path: str) -> None:
        if gate_armed.is_set() and not gate_reached.is_set() and _is_engine_bootstrap_script(path):
            gated_paths.append(path)
            gate_reached.set()
            release_gate.wait(timeout=60.0)
        return None

    with ViewerSession(resolve_target(watched), poll_interval=0.05) as session:
        session.load_initial()
        session.start()
        cache_depth = session.state._max_cached_snapshots
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with _DelayingProxy(server.host, server.port, on_request=on_request) as proxy:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 840})
                        console_errors: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                        page.goto(f"{proxy.url}?lang=en", wait_until="domcontentloaded")

                        revision = 1
                        if warm:
                            expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)
                            obs_a = _observe_visible_state(page)
                            _assert_coherent_a_or_b(obs_a, hash_a=hash_a, hash_b=hash_b)
                            assert obs_a["scene"]["project_hash"] == hash_a
                            gate_armed.set()
                            _replace_dir_from_subprocess(run_b, watched)
                            revision += 1
                            _wait_for_revision(session, revision)

                        assert gate_reached.wait(timeout=20.0), "the engine-view iframe never requested its engine script"
                        gated_snapshot = session.state.envelope()["snapshot"]["snapshot_id"]

                        # Publish more valid runs than the cache holds, each one observed by the watcher (a barrier,
                        # not a sleep), so the gated iframe's own snapshot is evicted before its script is released.
                        showing_b = warm
                        for _ in range(cache_depth + 1):
                            showing_b = not showing_b
                            _replace_dir_from_subprocess(run_b if showing_b else run_a, watched)
                            revision += 1
                            _wait_for_revision(session, revision)
                        if showing_b:
                            # End the burst on A so the final write below is a real change to B.
                            _replace_dir_from_subprocess(run_a, watched)
                            revision += 1
                            _wait_for_revision(session, revision)
                        built, _cur_id, _cur_rev = session.state.get_cached_snapshot(snapshot_id=gated_snapshot)
                        assert built is None, "the gated snapshot should have been evicted before release"
                        if warm:
                            # While held, the page keeps the verified previous frame (A) rather than another run's data.
                            obs_held = _observe_visible_state(page)
                            _assert_coherent_a_or_b(obs_held, hash_a=hash_a, hash_b=hash_b)
                            assert obs_held["scene"]["project_hash"] == hash_a, obs_held

                        release_gate.set()
                        # A final valid B, then no more writes.
                        _replace_dir_from_subprocess(run_b, watched)
                        revision += 1
                        _wait_for_revision(session, revision)
                        final = session.state.envelope()["snapshot"]
                        assert final["identity"]["project_hash"] == hash_b

                        expect(page.locator("#app")).to_have_attribute("data-snapshot-id", final["snapshot_id"], timeout=30000)
                        obs = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs, hash_a=hash_a, hash_b=hash_b)
                        assert obs["scene"]["project_hash"] == hash_b, obs
                        assert obs["scene"]["frame_snapshot_id"] == final["snapshot_id"], obs
                        assert obs["app_revision"] == str(revision), obs
                        assert gated_paths, "the bootstrap script was never gated"
                        # The evicted scene is refused (409) by design; nothing else may fail.
                        unexpected = [e for e in console_errors if "409" not in e]
                        assert unexpected == [], f"unexpected browser console errors: {console_errors}"
                    finally:
                        release_gate.set()
                        browser.close()


@pytest.mark.parametrize("failure", ["engine-script", "page"], ids=["engine-script-503", "engine-page-503"])
def test_failed_engine_bootstrap_is_abandoned_and_retried(browser_env, tmp_path, failure):
    """A scene frame whose engine script (or page) fails to load must release the single in-flight slot.

    The page keeps the verified previous frame (A) while it retries, then shows the valid latest run (B) with its
    ledger, without a refresh and without another write.
    """
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / f"watched_failed_{failure}"
    shutil.copytree(run_a, watched)

    armed = threading.Event()
    failed_paths: list[str] = []
    lock = threading.Lock()

    def on_request(path: str):
        if not armed.is_set():
            return None
        bare = path.split("?", 1)[0]
        hit = _is_engine_bootstrap_script(path) if failure == "engine-script" else (
            bare.startswith("/engine-view/rev-2-") and bare.endswith("/")
        )
        with lock:
            if hit and not failed_paths:
                failed_paths.append(path)
                return 503, b"injected failure"
        return None

    with ViewerSession(resolve_target(watched), poll_interval=0.05) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with _DelayingProxy(server.host, server.port, on_request=on_request) as proxy:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 840})
                        page.goto(f"{proxy.url}?lang=en", wait_until="domcontentloaded")
                        expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)

                        armed.set()
                        _replace_dir_from_subprocess(run_b, watched)
                        _wait_for_revision(session, 2)
                        final = session.state.envelope()["snapshot"]

                        expect(page.locator("#app")).to_have_attribute("data-snapshot-id", final["snapshot_id"], timeout=30000)
                        obs = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs, hash_a=hash_a, hash_b=hash_b)
                        assert obs["scene"]["project_hash"] == hash_b, obs
                        assert obs["scene"]["frame_snapshot_id"] == final["snapshot_id"], obs
                        with lock:
                            assert len(failed_paths) == 1, f"the injected failure was not exercised: {failed_paths}"
                    finally:
                        browser.close()


def test_viewer_restarted_on_same_port_is_followed_without_refresh(browser_env, tmp_path):
    """A viewer restarted on the same address numbers its states from 1 again: the open page must follow the new
    process (scene and ledger) instead of discarding its envelopes as older than the previous process's."""
    import socket

    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / "watched_restart"
    shutil.copytree(run_a, watched)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 840})
            with ViewerSession(resolve_target(watched), poll_interval=0.05) as session:
                session.load_initial()
                session.start()
                with LocalViewerServer(session, host="127.0.0.1", port=port, heartbeat_interval=1.0) as server:
                    page.goto(f"{server.url}?lang=en", wait_until="domcontentloaded")
                    expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)
                    # Advance the first process to B at rev 3, so its state_seq is ahead of a fresh process's.
                    for revision, src in ((2, run_b), (3, run_a), (4, run_b)):
                        _replace_dir_from_subprocess(src, watched)
                        _wait_for_revision(session, revision)
                    expect(page.locator("#app")).to_have_attribute("data-revision", "4", timeout=20000)
                    obs_old = _observe_visible_state(page)
                    assert obs_old["scene"]["project_hash"] == hash_b, obs_old

            # A new viewer process on the same port, now watching A again (rev 1, state_seq 1).
            _replace_dir_from_subprocess(run_a, watched)
            with ViewerSession(resolve_target(watched), poll_interval=0.05) as session:
                new_snap = session.load_initial().snapshot
                session.start()
                with LocalViewerServer(session, host="127.0.0.1", port=port, heartbeat_interval=1.0):
                    expect(page.locator("#app")).to_have_attribute("data-snapshot-id", new_snap["snapshot_id"], timeout=30000)
                    obs_new = _observe_visible_state(page)
                    _assert_coherent_a_or_b(obs_new, hash_a=hash_a, hash_b=hash_b)
                    assert obs_new["scene"]["project_hash"] == hash_a, obs_new
                    assert obs_new["app_revision"] == "1", obs_new
                    # Later edits on the new process are followed too.
                    _replace_dir_from_subprocess(run_b, watched)
                    _wait_for_revision(session, 2)
                    expect(page.locator("#app")).to_have_attribute("data-revision", "2", timeout=20000)
                    obs_next = _observe_visible_state(page)
                    _assert_coherent_a_or_b(obs_next, hash_a=hash_a, hash_b=hash_b)
                    assert obs_next["scene"]["project_hash"] == hash_b, obs_next
        finally:
            browser.close()


def test_snapshot_api_failure_is_retried_until_it_recovers(browser_env, tmp_path):
    """A failed /api/snapshot read (HTTP 503) must be retried on its own: once the API answers again the page shows
    the latest run (scene and ledger), clears the read error and is live again, without a refresh or another write."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, run_b, hash_a, hash_b = browser_env
    watched = tmp_path / "watched_api_503"
    shutil.copytree(run_a, watched)

    failing = threading.Event()
    failed_reads: list[str] = []

    def on_request(path: str):
        if failing.is_set() and path.split("?", 1)[0] == "/api/snapshot":
            failed_reads.append(path)
            return 503, b"injected failure"
        return None

    with ViewerSession(resolve_target(watched), poll_interval=0.05) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=1.0) as server:
            with _DelayingProxy(server.host, server.port, on_request=on_request) as proxy:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1280, "height": 840})
                        page.goto(f"{proxy.url}?lang=en", wait_until="domcontentloaded")
                        expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)

                        failing.set()
                        _replace_dir_from_subprocess(run_b, watched)
                        _wait_for_revision(session, 2)
                        expect(page.locator("#banner-error")).to_contain_text("/api/snapshot", timeout=15000)
                        obs_held = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs_held, hash_a=hash_a, hash_b=hash_b)
                        assert obs_held["scene"]["project_hash"] == hash_a, obs_held

                        # The API recovers; nothing is written any more, so no new event will arrive.
                        failing.clear()
                        final = session.state.envelope()["snapshot"]
                        expect(page.locator("#app")).to_have_attribute("data-snapshot-id", final["snapshot_id"], timeout=20000)
                        expect(page.locator("#banner-error")).to_be_hidden(timeout=10000)
                        expect(page.locator("#badge-live")).not_to_have_class(re.compile(r"\berr\b"), timeout=10000)
                        obs = _observe_visible_state(page)
                        _assert_coherent_a_or_b(obs, hash_a=hash_a, hash_b=hash_b)
                        assert obs["scene"]["project_hash"] == hash_b, obs
                        assert failed_reads, "the injected API failure was not exercised"
                    finally:
                        failing.clear()
                        browser.close()


# ------------------------------------------------------------------------------------------------------------------
# Project + saved run followed through a Project edit (stale) and a re-run (current again): kit #45 V1 and V2.

_HIDDEN_STATE_JS = """() => {
    const app = document.getElementById('app');
    const pill = (id) => {
        const n = document.getElementById(id);
        return {
            hidden: n.classList.contains('hidden'),
            text: n.textContent,
            aria_hidden: n.getAttribute('aria-hidden'),
            class_name: n.className,
        };
    };
    const hiddenWithText = [];
    document.querySelectorAll('.pill.hidden, #frame-updating.hidden').forEach((n) => {
        if ((n.textContent || '') !== '' || n.getAttribute('aria-hidden') !== 'true') {
            hiddenWithText.push(n.id || n.className);
        }
    });
    const attrs = {};
    for (const a of app.attributes) {
        if (a.name.startsWith('data-')) attrs[a.name] = a.value;
    }
    return {
        attrs: attrs,
        conserved: pill('badge-conserved'),
        freshness: pill('badge-freshness'),
        mode: pill('badge-mode'),
        geom_only: pill('badge-geom-only'),
        schematic: pill('badge-schematic'),
        updating: pill('frame-updating'),
        hidden_with_text: hiddenWithText,
        body_text: document.body.textContent,
        power_text: (document.getElementById('power-body')?.textContent || '').trim(),
    };
}"""

_CAMERA_JS = """() => {
    const f = document.querySelector('iframe.engine-frame.active');
    const v = f && f.contentWindow && f.contentWindow.__opensciViewer;
    if (!v) return null;
    const c = v.camera;
    return {
        orthographic: !!c.isOrthographicCamera,
        position: c.position.toArray(),
        target: v.controls.target.toArray(),
        up: c.up.toArray(),
        zoom: c.zoom,
        stage: [v.width, v.height],
        auto_framed: v.autoFrame !== null && v.autoFrame !== undefined,
        beams: Array.isArray(v.doc.beams) ? v.doc.beams.length : 0,
        frame_snapshot_id: f.getAttribute('data-snapshot-id') || '',
        camera_attr: f.getAttribute('data-camera') || '',
        iso_view_direction: (v.doc.camera_presets && v.doc.camera_presets.iso) ? v.doc.camera_presets.iso.view_direction : null,
    };
}"""

_STRINGS = {
    "en": {
        "conserved": "Power conserved",
        "not_conserved": "Residual outside tolerance",
        "current": "Current",
        "historical": "Historical",
        "geom_only": "Geometry Only",
        "stale_mode": "Stale Run · Project Updated",
        "computed_mode": "Computed Result",
    },
    "zh": {
        "conserved": "功率守恒",
        "not_conserved": "残差超容差",
        "current": "最新匹配",
        "historical": "历史过期",
        "geom_only": "仅几何预览",
        "stale_mode": "结果已过期 · 工程已更新",
        "computed_mode": "已计算结果",
    },
}


def _fixture_a_power(power_w: float) -> dict:
    doc = _fixture_a()
    for comp in doc["components"]:
        if comp["instance_id"] == "SRC":
            comp["source_model"]["power_w"] = power_w
    return doc


def _write_json_from_subprocess(path: Path, doc: dict) -> None:
    staged = path.parent / (path.name + ".src")
    staged.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _copy_file_from_subprocess(staged, path)
    staged.unlink()


@pytest.fixture(scope="module")
def stale_env(browser_env, tmp_path_factory):
    """Fixture A at 1 mW (the saved run) and at 3 mW (the Project edit and its re-run): same geometry, new physics."""
    from os_optics.local import run_optical_project
    from os_optics.results import load_run

    browser_path, run_a, _run_b, hash_a, _hash_b = browser_env
    base = tmp_path_factory.mktemp("viewer-stale")
    run_3mw = base / "run_3mw"
    run_optical_project(_fixture_a_power(0.003), output_dir=run_3mw)
    hash_3mw = load_run(run_3mw, verify=True).identity()["project_hash"]
    assert hash_3mw != hash_a
    return browser_path, run_a, hash_a, run_3mw, hash_3mw


def _snapshot_api(page, server_url: str) -> dict:
    response = page.request.get(f"{server_url}api/snapshot")
    assert response.ok, response.status
    return response.json()["snapshot"]


def _expected_attrs(snap: dict) -> dict:
    """The #app state attributes the page must show for an /api/snapshot snapshot (setStateAttributes in viewer.js)."""
    power = snap["power_ledger"]
    fresh = snap["freshness"] or {}
    scene = snap["scene"] or {}
    execution = snap["execution"] or {}

    def flag(value):
        return "true" if value is True else ("false" if value is False else "")

    return {
        "data-revision": str(snap["revision"]),
        "data-snapshot-id": snap["snapshot_id"],
        "data-result-mode": snap["result_mode"],
        "data-computed": flag(snap["is_computed_result"] is True),
        "data-freshness": str(fresh.get("state") or "") if fresh.get("checked") else "",
        "data-project-hash": snap["identity"].get("project_hash") or "",
        "data-execution-outcome": execution.get("outcome") or "",
        "data-power-available": flag(power["available"] is True),
        "data-power-conserved": flag(power["conserved"]) if power["available"] else "",
        "data-branch-count": str(len(snap["branches"])),
        "data-geometry-only": flag(scene.get("geometry_only") is True),
        "data-drawable-ray-count": str(scene["drawable_ray_count"]),
    }


def _assert_attrs_match_api(state: dict, snap: dict) -> None:
    expected = _expected_attrs(snap)
    shown = {k: state["attrs"].get(k) for k in expected}
    assert shown == expected, f"#app state attributes disagree with /api/snapshot: {shown} != {expected}"


def _assert_hidden_pill_empty(pill: dict, name: str) -> None:
    assert pill["hidden"], f"{name} should be hidden: {pill}"
    assert pill["text"] == "", f"hidden {name} keeps text in the DOM: {pill}"
    assert pill["aria_hidden"] == "true", f"hidden {name} is not aria-hidden: {pill}"
    assert pill["class_name"] == "pill hidden", f"hidden {name} keeps a tone class: {pill}"


def _assert_shown_pill(pill: dict, text: str, name: str) -> None:
    assert not pill["hidden"], f"{name} should be shown: {pill}"
    assert pill["text"] == text, f"{name} text: {pill}"
    assert pill["aria_hidden"] is None, f"shown {name} is aria-hidden: {pill}"


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_stale_project_hides_badges_without_text_and_states_agree(stale_env, tmp_path, lang):
    """Kit #45 V2: computed -> stale (Project edit) -> current (re-run). A hidden badge carries no text, no tone and is
    aria-hidden; the #app data attributes state the shown snapshot exactly as /api/snapshot does."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, hash_a, run_3mw, hash_3mw = stale_env
    words = _STRINGS[lang]
    project = tmp_path / "project.json"
    project.write_text(json.dumps(_fixture_a_power(0.001), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    shutil.copytree(run_a, run_dir)

    with ViewerSession(resolve_target(project, run=run_dir), poll_interval=0.05) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 840})
                    console_errors: list[str] = []
                    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                    page.goto(f"{server.url}?lang={lang}", wait_until="domcontentloaded")

                    # rev 1: the saved run, computed and current.
                    expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)
                    expect(page.locator("#app")).to_have_attribute("data-status", "ok", timeout=10000)
                    s1 = page.evaluate(_HIDDEN_STATE_JS)
                    api1 = _snapshot_api(page, server.url)
                    assert api1["result_mode"] == "computed_run" and api1["power_ledger"]["conserved"] is True, api1
                    _assert_shown_pill(s1["conserved"], words["conserved"], "#badge-conserved")
                    _assert_shown_pill(s1["freshness"], words["current"], "#badge-freshness")
                    _assert_shown_pill(s1["mode"], words["computed_mode"], "#badge-mode")
                    _assert_hidden_pill_empty(s1["geom_only"], "#badge-geom-only")
                    _assert_hidden_pill_empty(s1["schematic"], "#badge-schematic")
                    assert s1["hidden_with_text"] == [], s1["hidden_with_text"]
                    assert words["geom_only"] not in s1["body_text"]
                    _assert_attrs_match_api(s1, api1)
                    assert s1["attrs"]["data-project-hash"] == hash_a

                    # rev 2: the Project is edited (3 mW), the run is historical: rays, ledger and verdict withheld.
                    _write_json_from_subprocess(project, _fixture_a_power(0.003))
                    expect(page.locator("#app")).to_have_attribute("data-revision", "2", timeout=20000)
                    expect(page.locator("#app")).to_have_attribute("data-result-mode", "stale_run", timeout=10000)
                    s2 = page.evaluate(_HIDDEN_STATE_JS)
                    api2 = _snapshot_api(page, server.url)
                    assert api2["result_mode"] == "stale_run", api2["result_mode"]
                    assert api2["power_ledger"]["available"] is False and api2["power_ledger"]["conserved"] is None
                    _assert_hidden_pill_empty(s2["conserved"], "#badge-conserved")
                    _assert_shown_pill(s2["freshness"], words["historical"], "#badge-freshness")
                    _assert_shown_pill(s2["mode"], words["stale_mode"], "#badge-mode")
                    _assert_shown_pill(s2["geom_only"], words["geom_only"], "#badge-geom-only")
                    assert s2["hidden_with_text"] == [], s2["hidden_with_text"]
                    for stale_word in (words["conserved"], words["not_conserved"],
                                       _STRINGS["en"]["conserved"], _STRINGS["zh"]["conserved"]):
                        assert stale_word not in s2["body_text"], f"stale text {stale_word!r} left in the DOM"
                    assert "mW" not in s2["power_text"], s2["power_text"]
                    _assert_attrs_match_api(s2, api2)
                    assert s2["attrs"]["data-project-hash"] == hash_3mw
                    assert s2["attrs"]["data-power-available"] == "false"
                    assert s2["attrs"]["data-power-conserved"] == ""
                    assert s2["attrs"]["data-freshness"] == "HISTORICAL"

                    # rev 3: the edited Project is re-run into the same directory: current again.
                    _replace_dir_from_subprocess(run_3mw, run_dir)
                    expect(page.locator("#app")).to_have_attribute("data-revision", "3", timeout=20000)
                    expect(page.locator("#app")).to_have_attribute("data-result-mode", "computed_run", timeout=10000)
                    s3 = page.evaluate(_HIDDEN_STATE_JS)
                    api3 = _snapshot_api(page, server.url)
                    assert abs(api3["power_ledger"]["total_input_W"] - 0.003) < 1e-12
                    assert "3 mW" in s3["power_text"], s3["power_text"]
                    _assert_shown_pill(s3["conserved"], words["conserved"], "#badge-conserved")
                    _assert_shown_pill(s3["freshness"], words["current"], "#badge-freshness")
                    _assert_hidden_pill_empty(s3["geom_only"], "#badge-geom-only")
                    assert s3["hidden_with_text"] == [], s3["hidden_with_text"]
                    assert words["geom_only"] not in s3["body_text"]
                    _assert_attrs_match_api(s3, api3)
                    assert s3["attrs"]["data-project-hash"] == hash_3mw
                    assert console_errors == [], f"unexpected browser console errors: {console_errors}"
                finally:
                    browser.close()


def _direction(pose: dict) -> list[float]:
    d = [p - t for p, t in zip(pose["position"], pose["target"], strict=True)]
    n = sum(x * x for x in d) ** 0.5
    return [x / n for x in d]


def _max_diff(a: list[float], b: list[float]) -> float:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def _assert_same_pose(pose: dict, ref: dict, label: str) -> None:
    """Same camera: projection, line of sight, aim point and screen up. The eye position is the same too, except for a
    camera still framed by the viewer (never moved by the reader) on a drawing area of another size: the engine frames
    the same box again for the new area (its resize rule), which only moves the eye along the same line of sight."""
    assert pose is not None and ref is not None, (label, pose, ref)
    assert pose["orthographic"] == ref["orthographic"], (label, pose, ref)
    assert _max_diff(_direction(pose), _direction(ref)) <= 1e-9, f"{label}: line of sight turned: {pose} != {ref}"
    assert _max_diff(pose["target"], ref["target"]) <= 1e-6, f"{label}: aim point moved: {pose} != {ref}"
    assert _max_diff(pose["up"], ref["up"]) <= 1e-9, f"{label}: screen up turned: {pose} != {ref}"
    if pose["auto_framed"] and ref["auto_framed"] and pose["stage"] != ref["stage"]:
        return
    assert _max_diff(pose["position"], ref["position"]) <= 1e-6, f"{label}: eye moved: {pose} != {ref}"
    assert abs(pose["zoom"] - ref["zoom"]) <= 1e-9, (label, pose, ref)


@pytest.mark.parametrize("moved", [False, True], ids=["auto-framed", "user-moved"])
def test_camera_pose_survives_rays_to_geometry_only_and_back(stale_env, tmp_path, moved):
    """Kit #45 V1: the camera the reader looks through is kept when the view goes from traced rays (computed run) to
    geometry only (stale run) and back (re-run), whether the reader moved it or left the engine's framing."""
    from playwright.sync_api import expect, sync_playwright

    browser_path, run_a, hash_a, run_3mw, hash_3mw = stale_env
    project = tmp_path / "project.json"
    project.write_text(json.dumps(_fixture_a_power(0.001), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    shutil.copytree(run_a, run_dir)

    with ViewerSession(resolve_target(project, run=run_dir), poll_interval=0.05) as session:
        session.load_initial()
        session.start()
        with LocalViewerServer(session, host="127.0.0.1", port=0, heartbeat_interval=2.0) as server:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 840})
                    console_errors: list[str] = []
                    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                    page.goto(f"{server.url}?lang=en", wait_until="domcontentloaded")
                    expect(page.locator("#app")).to_have_attribute("data-revision", "1", timeout=20000)
                    expect(page.locator("#app")).to_have_attribute("data-status", "ok", timeout=10000)
                    pose1 = page.evaluate(_CAMERA_JS)
                    assert pose1 is not None and pose1["beams"] > 0, pose1

                    if moved:
                        # Orbit like a reader: a real drag on the 3D view (OrbitControls), then a wheel zoom.
                        box = page.locator("iframe.engine-frame.active").bounding_box()
                        cx, cy = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
                        page.mouse.move(cx, cy)
                        page.mouse.down()
                        for step in range(1, 11):
                            page.mouse.move(cx + 14 * step, cy + 6 * step)
                        page.mouse.up()
                        page.mouse.wheel(0, -240)
                        page.wait_for_timeout(300)
                        moved_pose = page.evaluate(_CAMERA_JS)
                        assert not moved_pose["auto_framed"], moved_pose
                        assert _max_diff(moved_pose["position"], pose1["position"]) > 1.0, (moved_pose, pose1)
                        reference = moved_pose
                    else:
                        reference = pose1

                    # rev 2: geometry only (stale run).
                    _write_json_from_subprocess(project, _fixture_a_power(0.003))
                    expect(page.locator("#app")).to_have_attribute("data-revision", "2", timeout=20000)
                    pose2 = page.evaluate(_CAMERA_JS)
                    assert pose2["beams"] == 0 and pose2["frame_snapshot_id"] != pose1["frame_snapshot_id"], pose2
                    _assert_same_pose(pose2, reference, "rays -> geometry only")
                    assert pose2["auto_framed"] == (not moved), pose2

                    # rev 3: traced rays again (re-run).
                    _replace_dir_from_subprocess(run_3mw, run_dir)
                    expect(page.locator("#app")).to_have_attribute("data-revision", "3", timeout=20000)
                    pose3 = page.evaluate(_CAMERA_JS)
                    assert pose3["beams"] > 0, pose3
                    _assert_same_pose(pose3, reference, "geometry only -> rays")

                    # A resize keeps the line of sight: an auto-framed camera is framed again on the same box, a camera
                    # the reader moved stays aimed where it was.
                    page.set_viewport_size({"width": 1180, "height": 800})
                    page.wait_for_timeout(300)
                    pose4 = page.evaluate(_CAMERA_JS)
                    assert _max_diff(_direction(pose4), _direction(reference)) <= 1e-6, (pose4, reference)
                    if moved:
                        assert _max_diff(pose4["target"], reference["target"]) <= 1e-6, (pose4, reference)

                    if not moved:
                        # Nothing to carry (a page opened on a stale revision): the geometry-only view still starts on
                        # the traced view's direction, as it is framed like the historical run's view.
                        _write_json_from_subprocess(project, _fixture_a_power(0.002))
                        expect(page.locator("#app")).to_have_attribute("data-revision", "4", timeout=20000)
                        fresh = browser.new_page(viewport={"width": 1280, "height": 840})
                        fresh.goto(f"{server.url}?lang=en", wait_until="domcontentloaded")
                        expect(fresh.locator("#app")).to_have_attribute("data-result-mode", "stale_run", timeout=20000)
                        expect(fresh.locator("#app")).to_have_attribute("data-status", "ok", timeout=10000)
                        fresh_pose = fresh.evaluate(_CAMERA_JS)
                        assert fresh_pose["beams"] == 0 and fresh_pose["camera_attr"] == "default", fresh_pose
                        assert fresh_pose["iso_view_direction"] == pose3["iso_view_direction"], (fresh_pose, pose3)
                        assert _max_diff(_direction(fresh_pose), _direction(pose1)) <= 1e-6, (fresh_pose, pose1)
                        assert _max_diff(fresh_pose["up"], pose1["up"]) <= 1e-6, (fresh_pose, pose1)
                    # The page states which frames took the reader's camera: the first frame keeps the engine's framing.
                    assert (pose1["camera_attr"], pose2["camera_attr"], pose3["camera_attr"]) == (
                        "default", "carried", "carried"), (pose1, pose2, pose3)
                    assert console_errors == [], f"unexpected browser console errors: {console_errors}"
                finally:
                    browser.close()
