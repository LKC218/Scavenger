"""隔离端口验证《启动Scavenger.cmd》与《启动主服务.py》的启停、防代理、热更新、防误杀与日志滚动。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import 启动主服务 as main_srv  # noqa: E402

_NO_PROXY_OPENER = build_opener(ProxyHandler({}))
CMD_FILE = ROOT / "启动Scavenger.cmd"
SERVER_SCRIPT = ROOT / "scripts" / "启动主服务.py"


def alloc_free_port() -> int:
    """动态申请一个非 8800 的空闲本地回环端口。"""
    for _ in range(16):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])
        if port != 8800:
            return port
    raise RuntimeError("未能分配到非 8800 的空闲测试端口")


def run_server_cli(args: list[str], extra_env: dict[str, str] | None = None, timeout: float = 20.0) -> subprocess.CompletedProcess:
    """以参数列表方式执行主服务 CLI。"""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(SERVER_SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def run_cmd_entry(args: list[str], timeout: float = 25.0) -> subprocess.CompletedProcess:
    """通过 Windows cmd.exe 真实调用根目录 启动Scavenger.cmd。"""
    env = os.environ.copy()
    env["SCAVENGER_NO_PAUSE"] = "1"
    return subprocess.run(
        ["cmd", "/c", str(CMD_FILE), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def cleanup_test_artifacts(port: int) -> None:
    """停止测试端口上可能残留的本项目进程并清理该测试端口对应状态/日志/锁文件。"""
    if port == 8800:
        return
    try:
        main_srv.stop_service(port)
    except Exception:
        pass
    for fp in (
        main_srv.runtime_state_file(port),
        main_srv.runtime_log_file(port),
        main_srv.runtime_lock_file(port),
    ):
        try:
            if fp.exists():
                fp.unlink()
        except OSError:
            pass


def http_get(url: str, timeout: float = 5.0, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req_headers = {"Cache-Control": "no-cache", "Connection": "close"}
    if headers:
        req_headers.update(headers)
    req = Request(url, headers=req_headers)
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read()
    except HTTPError as err:
        return int(err.code), err.read()


def http_post(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> tuple[int, str]:
    req_headers = {"Content-Type": "application/json", "Connection": "close"}
    if headers:
        req_headers.update(headers)
    req = Request(url, data=b"{}", method="POST", headers=req_headers)
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read().decode("utf-8", errors="replace")
    except HTTPError as err:
        return int(err.code), err.read().decode("utf-8", errors="replace")


def main() -> int:
    results: list[dict] = []
    test_port = alloc_free_port()
    stale_port = alloc_free_port()
    foreign_port = alloc_free_port()
    cmd_port = alloc_free_port()
    fg_port = alloc_free_port()
    pure_port = alloc_free_port()
    while len({test_port, stale_port, foreign_port, cmd_port, fg_port, pure_port, 8800}) < 7:
        stale_port = alloc_free_port()
        foreign_port = alloc_free_port()
        cmd_port = alloc_free_port()
        fg_port = alloc_free_port()
        pure_port = alloc_free_port()

    foreign_proc = None
    stale_proc = None
    fg_proc = None

    def record(name: str, passed: bool, details: object = None) -> None:
        item = {"test": name, "passed": bool(passed), "details": details}
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(f"验证项未通过：{name} -> {details}")

    try:
        # 1. 启动Scavenger.cmd 编码、CRLF 换行及默认前台常驻命令检查
        raw_cmd = CMD_FILE.read_bytes()
        cmd_text = raw_cmd.decode("utf-8")
        has_bom = raw_cmd.startswith(b"\xef\xbb\xbf")
        crlf_count = raw_cmd.count(b"\r\n")
        bare_lf = raw_cmd.replace(b"\r\n", b"").count(b"\n")
        has_fg_default = "serve --open --panel" in cmd_text
        record(
            "01-批处理UTF8无BOM与严格CRLF换行及默认常驻参数",
            not has_bom and crlf_count > 10 and bare_lf == 0 and has_fg_default,
            {"bytes": len(raw_cmd), "has_bom": has_bom, "crlf_count": crlf_count, "bare_lf": bare_lf, "fg_default": has_fg_default},
        )

        # 2. 未启动时 status 返回非零且 running=false
        cleanup_test_artifacts(test_port)
        p_status_idle = run_server_cli(["status", "--port", str(test_port)])
        idle_json = json.loads(p_status_idle.stdout.strip())
        record(
            "02-未启动时status返回非零及未占用状态",
            p_status_idle.returncode == 1 and idle_json.get("running") is False and idle_json.get("port_occupied") is False,
            {"returncode": p_status_idle.returncode, "status": idle_json},
        )

        # 3. start 启动测试端口服务与 /healthz 及状态文件核验
        p_start = run_server_cli(["start", "--port", str(test_port), "--json"])
        start_json = json.loads(p_start.stdout.strip())
        state_file = main_srv.runtime_state_file(test_port)
        state_data = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
        code_hz, raw_hz = http_get(f"http://127.0.0.1:{test_port}/healthz")
        hz = json.loads(raw_hz.decode("utf-8"))
        req_hz_keys = {"status", "service", "version", "launcher_version", "port", "pid", "started_at", "uptime_seconds", "script_sha256", "root_sha256", "ui_spec_version", "render_baseline", "source_sha256", "routes"}
        record(
            "03-start后台启动与healthz及运行状态文件核验",
            p_start.returncode == 0
            and start_json.get("ok") is True
            and start_json.get("action") == "started"
            and code_hz == 200
            and req_hz_keys.issubset(hz.keys())
            and state_data.get("pid") == hz.get("pid"),
            {"pid": hz.get("pid"), "state_file": state_file.relative_to(ROOT).as_posix()},
        )

        # 4. 坏系统代理环境变量免疫测试
        t0 = time.monotonic()
        p_proxy = run_server_cli(
            ["status", "--port", str(test_port)],
            extra_env={
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "http_proxy": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9",
            },
        )
        elapsed_proxy = round(time.monotonic() - t0, 3)
        proxy_json = json.loads(p_proxy.stdout.strip())
        record(
            "04-坏系统代理免疫测试",
            p_proxy.returncode == 0 and proxy_json.get("running") is True and elapsed_proxy < 3.0,
            {"elapsed_s": elapsed_proxy, "running": proxy_json.get("running")},
        )

        # 5. 内部关停接口鉴权与 405 校验
        c_no_tok, _ = http_post(f"http://127.0.0.1:{test_port}/__internal/shutdown")
        c_bad_tok, _ = http_post(
            f"http://127.0.0.1:{test_port}/__internal/shutdown",
            headers={"X-Scavenger-Token": "wrong-token-123"},
        )
        c_other_post, _ = http_post(f"http://127.0.0.1:{test_port}/healthz")
        _, raw_hz_after = http_get(f"http://127.0.0.1:{test_port}/healthz")
        hz_after = json.loads(raw_hz_after.decode("utf-8"))
        record(
            "05-内部关停接口403鉴权与存活保护",
            c_no_tok == 403 and c_bad_tok == 403 and c_other_post == 405 and hz_after.get("pid") == hz.get("pid"),
            {"no_token": c_no_tok, "bad_token": c_bad_tok, "other_post": c_other_post},
        )

        # 6. 核心路由 HTTP 200 回归
        route_codes: dict[str, int] = {}
        for rt in (
            "/healthz",
            "/api/status",
            "/",
            "/armory",
            "/settings",
            "/play",
            "/editor",
            "/ui-spec",
            "/ui/令牌.css",
            "/dev/battlefield",
            "/dev/library/",
            "/dev/audio/",
        ):
            from urllib.parse import quote

            sc, _ = http_get(f"http://127.0.0.1:{test_port}{quote(rt)}")
            route_codes[rt] = sc
        record(
            "06-核心页面与接口路由HTTP200回归",
            all(v == 200 for v in route_codes.values()),
            route_codes,
        )

        # 7. restart 平滑重启测试
        pid_before = hz["pid"]
        p_restart = run_server_cli(["restart", "--port", str(test_port), "--json"])
        restart_json = json.loads(p_restart.stdout.strip())
        pid_after = restart_json.get("pid")
        _, raw_hz2 = http_get(f"http://127.0.0.1:{test_port}/healthz")
        hz2 = json.loads(raw_hz2.decode("utf-8"))
        record(
            "07-restart平滑重启并切换新PID",
            p_restart.returncode == 0
            and restart_json.get("ok") is True
            and restart_json.get("action") == "restarted"
            and pid_after != pid_before
            and hz2.get("pid") == pid_after,
            {"pid_before": pid_before, "pid_after": pid_after},
        )

        # 先关闭 test_port 服务
        p_stop_test = run_server_cli(["stop", "--port", str(test_port), "--json"])
        stop_json = json.loads(p_stop_test.stdout.strip())
        if p_stop_test.returncode != 0 or not stop_json.get("stopped"):
            raise RuntimeError(f"停止测试服务失败：{p_stop_test.stdout} {p_stop_test.stderr}")

        # 8. 代码更新过期实例自动平滑重启测试（纯函数单测 + 端到端模拟过期实例）
        stale_unit_ok = (
            main_srv.is_stale_instance({"script_sha256": "0" * 64}, None, main_srv.compute_script_sha256()) is True
            and main_srv.is_stale_instance({"version": "V1.0"}, None, main_srv.compute_script_sha256()) is True
            and main_srv.is_stale_instance({"script_sha256": main_srv.compute_script_sha256()}, None, main_srv.compute_script_sha256()) is False
        )
        cleanup_test_artifacts(stale_port)
        fake_stale_code = (
            "import sys; sys.path.insert(0, 'scripts'); "
            "import 启动主服务 as m; "
            "m.compute_script_sha256 = lambda: '0' * 64; "
            "sys.argv = ['scripts/启动主服务.py', '--port', sys.argv[1], '--run-mode', 'background']; "
            "sys.exit(m.main())"
        )
        stale_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-B", "-c", fake_stale_code, str(stale_port)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        hz_stale = None
        wait_deadline = time.monotonic() + 12.0
        while time.monotonic() < wait_deadline:
            if stale_proc.poll() is not None:
                err_msg = stale_proc.stderr.read() if stale_proc.stderr else ""
                raise RuntimeError(f"模拟旧版进程提前退出({stale_proc.returncode})：{err_msg}")
            cand = main_srv.query_healthz("127.0.0.1", stale_port, timeout=0.5)
            if cand and cand.get("script_sha256") == "0" * 64:
                hz_stale = cand
                break
            time.sleep(0.1)
        if hz_stale is None:
            raise RuntimeError(f"模拟旧版进程在 12s 内未就绪（port={stale_port}）")
        old_stale_pid = stale_proc.pid
        p_upgrade = run_server_cli(["start", "--port", str(stale_port), "--json"])
        upgrade_json = json.loads(p_upgrade.stdout.strip())
        _, raw_hz_new = http_get(f"http://127.0.0.1:{stale_port}/healthz")
        hz_new = json.loads(raw_hz_new.decode("utf-8"))
        cleanup_test_artifacts(stale_port)
        record(
            "08-检测过期脚本实例并自动平滑热重启",
            stale_unit_ok
            and p_upgrade.returncode == 0
            and upgrade_json.get("action") == "restarted"
            and upgrade_json.get("restarted_reason") == "code_updated"
            and hz_new.get("pid") != old_stale_pid
            and hz_new.get("script_sha256") == main_srv.compute_script_sha256(),
            {"old_pid": old_stale_pid, "new_pid": hz_new.get("pid"), "reason": upgrade_json.get("restarted_reason")},
        )

        # 9. 非 Scavenger 外部进程占端口只诊断不强杀测试
        foreign_code = (
            "import socket, sys, time; "
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); "
            "s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen(4); "
            "print('READY', flush=True); "
            "time.sleep(60)"
        )
        foreign_proc = subprocess.Popen(
            [sys.executable, "-u", "-c", foreign_code, str(foreign_port)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        assert foreign_proc.stdout is not None
        ready_line = foreign_proc.stdout.readline().strip()
        if ready_line != "READY":
            raise RuntimeError(f"外部模拟占口进程启动异常：{ready_line}")

        foreign_checks = {}
        all_foreign_ok = True
        for subcmd in ("start", "stop", "restart"):
            t_f0 = time.monotonic()
            pf = run_server_cli([subcmd, "--port", str(foreign_port)])
            dt_f = round(time.monotonic() - t_f0, 3)
            still_alive = foreign_proc.poll() is None
            diag_has_pid = str(foreign_proc.pid) in (pf.stderr + pf.stdout)
            ok_sub = pf.returncode != 0 and dt_f < 5.0 and still_alive and diag_has_pid
            all_foreign_ok = all_foreign_ok and ok_sub
            foreign_checks[subcmd] = {
                "returncode": pf.returncode,
                "elapsed_s": dt_f,
                "alive": still_alive,
                "diag_has_pid": diag_has_pid,
            }
        record("09-外部占口进程只诊断不强杀保护", all_foreign_ok, {"foreign_pid": foreign_proc.pid, "checks": foreign_checks})

        # 10. 日志滚动截断 + 批处理启动Scavenger.cmd真实全流程
        rot_log = main_srv.runtime_log_file(cmd_port)
        rot_log.parent.mkdir(parents=True, exist_ok=True)
        filler = ("2026-11-22 [INFO] 日志填充行用以验证自动截断滚动行为\n" * 12000).encode("utf-8")
        sentinel_tail = "SENTINEL_LAST_LINE_KEEP\n".encode("utf-8")
        rot_log.write_bytes(filler + sentinel_tail)
        before_bytes = rot_log.stat().st_size
        rotated = main_srv.rotate_log_if_needed(rot_log, max_bytes=main_srv.LOG_MAX_BYTES)
        after_bytes = rot_log.stat().st_size
        tail_ok = "SENTINEL_LAST_LINE_KEEP" in rot_log.read_text(encoding="utf-8")

        cleanup_test_artifacts(cmd_port)
        c_start = run_cmd_entry(["start", "--port", str(cmd_port)])
        c_stat1 = run_cmd_entry(["status", "--port", str(cmd_port)])
        c_rest = run_cmd_entry(["restart", "--port", str(cmd_port)])
        c_stop = run_cmd_entry(["stop", "--port", str(cmd_port)])
        c_stat2 = run_cmd_entry(["status", "--port", str(cmd_port)])

        cmd_ok = (
            rotated
            and before_bytes > main_srv.LOG_MAX_BYTES
            and after_bytes <= main_srv.LOG_MAX_BYTES
            and tail_ok
            and c_start.returncode == 0
            and c_stat1.returncode == 0
            and c_rest.returncode == 0
            and c_stop.returncode == 0
            and c_stat2.returncode == 1
        )
        record(
            "10-日志滚动截断与批处理全流程调用",
            cmd_ok,
            {
                "log_before": before_bytes,
                "log_after": after_bytes,
                "tail_ok": tail_ok,
                "cmd_codes": [c_start.returncode, c_stat1.returncode, c_rest.returncode, c_stop.returncode, c_stat2.returncode],
            },
        )

        # 11. 前台常驻接管后台实例、保护已有前台窗口、关闭控制台窗口即释放端口测试
        cleanup_test_artifacts(fg_port)
        bg_start = run_server_cli(["start", "--port", str(fg_port), "--json"])
        bg_json = json.loads(bg_start.stdout.strip())
        bg_pid = bg_json.get("pid")

        fg_out_path = main_srv.RUNTIME_DIR / f"fg-panel-{fg_port}.tmp"
        fg_out_path.parent.mkdir(parents=True, exist_ok=True)
        fg_env = os.environ.copy()
        fg_env["SCAVENGER_NO_PAUSE"] = "1"
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        with fg_out_path.open("wb") as fg_stream:
            fg_proc = subprocess.Popen(
                ["cmd", "/c", str(CMD_FILE), "serve", "--panel", "--port", str(fg_port)],
                cwd=ROOT,
                env=fg_env,
                stdout=fg_stream,
                stderr=subprocess.STDOUT,
                startupinfo=si,
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )

        fg_hz = None
        fg_deadline = time.monotonic() + 10.0
        while time.monotonic() < fg_deadline:
            if fg_proc.poll() is not None:
                break
            hz_cand = main_srv.query_healthz("127.0.0.1", fg_port, timeout=0.4)
            if hz_cand and hz_cand.get("mode") == "foreground" and hz_cand.get("pid") != bg_pid:
                fg_hz = hz_cand
                break
            time.sleep(0.1)

        fg_pid = fg_hz.get("pid") if isinstance(fg_hz, dict) else None
        fg_state = main_srv.read_runtime_state(fg_port) or {}
        takeover_ok = (
            bg_start.returncode == 0
            and isinstance(fg_hz, dict)
            and fg_hz.get("mode") == "foreground"
            and fg_pid not in (None, bg_pid)
            and fg_state.get("mode") == "foreground"
            and fg_state.get("pid") == fg_pid
        )

        # 验证已有最新前台窗口运行期间再次拉起不会抢杀原窗口
        dup_proc = run_server_cli(["serve", "--panel", "--json", "--port", str(fg_port)])
        dup_json = json.loads(dup_proc.stdout.strip()) if dup_proc.stdout.strip() else {}
        reuse_ok = (
            dup_proc.returncode == 0
            and dup_json.get("status") == "reused_foreground"
            and dup_json.get("pid") == fg_pid
            and fg_proc.poll() is None
        )

        # 向隐藏控制台窗口发送真正的 Windows WM_CLOSE，验证关闭窗口即触发停服释放端口且删除状态文件
        t_close0 = time.monotonic()
        close_helper = (
            "import ctypes, sys; "
            "pid = int(sys.argv[1]); "
            "k32 = ctypes.windll.kernel32; u32 = ctypes.windll.user32; "
            "k32.FreeConsole(); ok = k32.AttachConsole(pid); "
            "hwnd = k32.GetConsoleWindow() if ok else 0; "
            "posted = bool(hwnd and u32.PostMessageW(hwnd, 0x0010, 0, 0)); "
            "k32.FreeConsole(); "
            "sys.exit(0 if posted else 1)"
        )
        if sys.platform == "win32":
            close_res = subprocess.run(
                [sys.executable, "-c", close_helper, str(fg_proc.pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5,
            )
            if close_res.returncode not in (0, 3221225786):
                raise RuntimeError(f"发送 WM_CLOSE 失败：returncode={close_res.returncode}")
        else:
            fg_proc.terminate()

        fg_proc.wait(timeout=6.0)
        close_elapsed = round(time.monotonic() - t_close0, 3)

        port_released = main_srv.wait_port_closed(fg_port, timeout=3.0)
        state_cleaned = not main_srv.runtime_state_file(fg_port).exists()
        fg_out = fg_out_path.read_text(encoding="utf-8", errors="replace") if fg_out_path.is_file() else ""
        try:
            if fg_out_path.exists():
                fg_out_path.unlink()
        except OSError:
            pass
        panel_ok = "前台常驻模式" in fg_out and "关闭此控制台窗口" in fg_out
        exit_ok = fg_proc.returncode in (0, 3221225786, -1073741510)

        record(
            "11-前台常驻接管与关闭窗口即释放端口及状态文件",
            takeover_ok and reuse_ok and port_released and state_cleaned and panel_ok and exit_ok,
            {
                "bg_pid": bg_pid,
                "fg_pid": fg_pid,
                "takeover_ok": takeover_ok,
                "reuse_ok": reuse_ok,
                "close_elapsed_s": close_elapsed,
                "port_released": port_released,
                "state_cleaned": state_cleaned,
                "panel_ok": panel_ok,
                "fg_exit": fg_proc.returncode,
            },
        )

        # 12. 纯净仓库模式（--assets-source public）双轨热切换、Range 206、音频清单与 local-data 严格隔离核验
        cleanup_test_artifacts(pure_port)
        p_auto = run_server_cli(["start", "--port", str(pure_port), "--assets-source", "auto", "--json"])
        auto_json = json.loads(p_auto.stdout.strip())
        p_pub = run_server_cli(["start", "--port", str(pure_port), "--assets-source", "public", "--json"])
        pub_json = json.loads(p_pub.stdout.strip())
        pub_hz = pub_json.get("healthz") or {}
        eff_assets = pub_hz.get("effective_assets") or {}

        pure_base = f"http://127.0.0.1:{pure_port}"
        st_b4, b4_chunk = http_get(f"{pure_base}/b4/startup-base.scvmap", headers={"Range": "bytes=0-1023"})
        st_v3, v3_chunk = http_get(f"{pure_base}/v3/vehicles-detail.scvveh", headers={"Range": "bytes=0-1023"})
        st_vmap, vmap_raw = http_get(f"{pure_base}/api/vehicle-audio-map")
        vmap_obj = json.loads(vmap_raw.decode("utf-8")) if st_vmap == 200 else {}
        audio_mf_path = ROOT / "public" / "assets" / "audio" / "runtime-audio-manifest.json"
        audio_mf = json.loads(audio_mf_path.read_text(encoding="utf-8")) if audio_mf_path.is_file() else {}
        sample_audio_urls = sorted((audio_mf.get("media_routes") or {}).keys())[:5]
        sample_audio_ok = bool(sample_audio_urls) and all(
            http_get(f"{pure_base}{u}")[0] == 200 for u in sample_audio_urls
        )
        st_cat, cat_raw = http_get(f"{pure_base}/api/catalog")
        cat_obj = json.loads(cat_raw.decode("utf-8")) if cat_raw else {}
        st_dev_audio, _ = http_get(f"{pure_base}{quote('/dev/audio/数据/音频资源.json', safe='/')}")
        p_pure_stop = run_server_cli(["stop", "--port", str(pure_port), "--json"])

        pure_ok = (
            p_auto.returncode == 0
            and p_pub.returncode == 0
            and pub_json.get("action") == "restarted"
            and pub_json.get("restarted_reason") == "assets_source_changed:auto->public"
            and pub_json.get("pid") != auto_json.get("pid")
            and pub_hz.get("assets_source") == "public"
            and all(eff_assets.get(k) == "public" for k in ("three", "map_b4", "vehicles_v3", "audio"))
            and st_b4 == 206
            and len(b4_chunk) == 1024
            and st_v3 == 206
            and len(v3_chunk) == 1024
            and st_vmap == 200
            and int(vmap_obj.get("total_vehicles", 0)) >= 100
            and sample_audio_ok
            and st_cat == 503
            and cat_obj.get("degraded") is True
            and st_dev_audio == 404
            and p_pure_stop.returncode == 0
        )
        record(
            "12-纯净仓库模式双轨切换与Range及隔离核验",
            pure_ok,
            {
                "pid_auto": auto_json.get("pid"),
                "pid_public": pub_json.get("pid"),
                "reason": pub_json.get("restarted_reason"),
                "effective_assets": eff_assets,
                "b4_range": (st_b4, len(b4_chunk)),
                "v3_range": (st_v3, len(v3_chunk)),
                "audio_vehicles": vmap_obj.get("total_vehicles"),
                "sample_audio_ok": sample_audio_ok,
                "catalog_degraded": (st_cat, cat_obj.get("degraded")),
                "dev_audio_isolated_status": st_dev_audio,
            },
        )
    finally:
        if foreign_proc is not None and foreign_proc.poll() is None:
            foreign_proc.terminate()
            try:
                foreign_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                foreign_proc.kill()
        if stale_proc is not None and stale_proc.poll() is None:
            stale_proc.terminate()
        if fg_proc is not None and fg_proc.poll() is None:
            fg_proc.terminate()
            try:
                fg_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                fg_proc.kill()
        for p in (test_port, stale_port, foreign_port, cmd_port, fg_port, pure_port):
            cleanup_test_artifacts(p)
        tmp_panel = main_srv.RUNTIME_DIR / f"fg-panel-{fg_port}.tmp"
        if tmp_panel.exists():
            try:
                tmp_panel.unlink()
            except OSError:
                pass

    print(json.dumps({"all_passed": True, "total_tests": len(results)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
