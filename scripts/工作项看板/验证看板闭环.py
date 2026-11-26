"""本地工作项看板端到端自动化闭环验证脚本（在隔离临时端口与临时数据库运行，零污染正式数据）。

验证项：
1. /healthz 指纹与静态页面（index.html / styles.css / app.mjs / favicon.svg）零缺损分发；
2. 预置 SCAV 示例项目、模块上下文包与 SCAV-4 ↔ SCAV-5 作用域路径重叠冲突自动检测；
3. REST CRUD、整型 rev 乐观锁 409 冲突拦截、严格模式打回原因强校验；
4. SHA-256 内容寻址附件上传去重与二进制回读一致；
5. SSE (/api/v1/events) 实时推送事件到达验证；
6. AI Agent CLI（看板工具.py）全流程子命令（meta / create / pull / claim / submit-plan / approve-plan / heartbeat / submit-qa / done / conflicts）；
7. 局域网模式（lan_pin）未登录 401 拦截、PIN 登录 Cookie 通行与 X-Board-Token 鉴权通行。
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from 数据库 import BoardStore  # noqa: E402
from 启动看板服务 import WorkboardServer, load_or_create_session_secret  # noqa: E402

_OPENER = build_opener(ProxyHandler({}))
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2ZkAAAAASUVORK5CYII="
)


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_json(port: int, method: str, path: str, body: dict | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict, dict]:
    hdrs = {"Accept": "application/json", "Connection": "close"}
    if headers:
        hdrs.update(headers)
    raw = None
    if body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = Request(f"http://127.0.0.1:{port}{path}", data=raw, headers=hdrs, method=method)
    try:
        with _OPENER.open(req, timeout=8.0) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read().decode("utf-8"))


def run_cli_raw(port: int, *cli_args: str, token: str = "") -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "-B",
        str(SCRIPT_DIR / "看板工具.py"),
        "--url",
        f"http://127.0.0.1:{port}",
    ]
    if token:
        cmd.extend(["--token", token])
    cmd.extend(cli_args)
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=15)


def run_cli(port: int, *cli_args: str, token: str = "") -> dict | str:
    proc = run_cli_raw(port, *cli_args, token=token)
    if proc.returncode != 0:
        raise AssertionError(f"CLI 执行失败 ({cli_args}): {proc.stderr or proc.stdout}")
    out = proc.stdout.strip()
    if "--json" in cli_args:
        return json.loads(out)
    return out


def verify_local_single_mode(tmp_dir: Path) -> None:
    port = pick_free_port()
    store = BoardStore(tmp_dir / "数据" / "workboard.db", seed_workspace=True)
    server = WorkboardServer(
        ("127.0.0.1", port),
        store=store,
        data_dir=tmp_dir,
        host="127.0.0.1",
        auth_mode="local_single",
        control_token="test-ctrl",
        session_secret=load_or_create_session_secret(tmp_dir),
    )
    th = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    try:
        # 1. /healthz、看板静态资源与 Code SSOT V2.2 /ui/* 单源只读挂载
        st, _, hz = http_json(port, "GET", "/healthz")
        assert st == 200 and hz["service"] == "scavenger-workboard" and hz["port"] == port
        for static_path in (
            "/",
            "/styles.css",
            "/app.mjs",
            "/favicon.svg",
            "/ui/%E4%BB%A4%E7%89%8C.css",
            "/ui/%E7%BB%84%E4%BB%B6.css",
            "/ui/%E4%BA%A4%E4%BA%92%E6%8E%A7%E4%BB%B6.mjs",
            "/ui/%E5%9B%BE%E6%A0%87%E4%B8%8E%E6%A0%87%E7%BB%98.mjs",
        ):
            with _OPENER.open(f"http://127.0.0.1:{port}{static_path}", timeout=5.0) as resp:
                assert resp.status == 200 and len(resp.read()) > 100

        # 2. 预置工作区：零假工作项、next_seq=1、5 大模块分类 × 13 个 Scavenger 实战模块 + 8 标准标签
        st, _, boot = http_json(port, "GET", "/api/v1/bootstrap")
        assert st == 200 and len(boot["projects"]) == 1 and len(boot["members"]) == 4
        assert len(boot["module_categories"]) == 5
        pid = boot["projects"][0]["id"]
        st, _, pitems = http_json(port, "GET", f"/api/v1/projects/{pid}/items")
        assert st == 200 and len(pitems["items"]) == 0 and pitems["project"]["next_seq"] == 1
        assert len(pitems["modules"]) == 13
        label_names = {lb["name"] for lb in pitems["labels"]}
        for must_lb in ("功能需求", "缺陷修复", "性能优化", "UI/UX", "AI协作", "资产管线", "数值/规则", "需评审"):
            assert must_lb in label_names, f"缺少预置标准标签：{must_lb}"
        cats = {m["category"] for m in pitems["modules"]}
        assert len(cats) == 5, f"预期 5 个模块分类，实际 {cats}"

        # 2b. 新增自定义分类模块并验证返回
        st, _, custom_mod = http_json(port, "POST", f"/api/v1/projects/{pid}/modules", {
            "category": "自定义专项",
            "name": "测试模块X",
            "summary": "校验自定义分类持久化",
            "entry_paths": ["scripts/工作项看板/"],
        })
        assert st == 201 and custom_mod["module"]["category"] == "自定义专项"

        # 2c. 构造两条处于进行中且路径重叠的测试卡，验证作用域路径防撞雷达
        st, _, c_a = http_json(port, "POST", f"/api/v1/projects/{pid}/items", {
            "title": "路径防撞验证A",
            "state": "in_progress",
            "scope_paths": ["src/交互系统/指令与标绘层.mjs"],
        })
        st, _, c_b = http_json(port, "POST", f"/api/v1/projects/{pid}/items", {
            "title": "路径防撞验证B",
            "state": "in_progress",
            "scope_paths": ["src/交互系统/"],
        })
        assert c_a["item"]["item_key"] == "SCAV-1" and c_b["item"]["item_key"] == "SCAV-2"
        st, _, pitems_after = http_json(port, "GET", f"/api/v1/projects/{pid}/items")
        assert len(pitems_after["conflict_pairs"]) >= 1, "预期 SCAV-1 与 SCAV-2 触发作用域路径重叠冲突"

        # 3. 乐观锁 rev 409 验证
        st, _, created = http_json(port, "POST", f"/api/v1/projects/{pid}/items", {
            "title": "自动化验证单据",
            "priority": "high",
            "scope_paths": ["src/工作项看板/应用.mjs"],
            "description_md": "校验乐观锁与严格模式",
        })
        assert st == 201
        key = created["item"]["item_key"]
        rev1 = created["item"]["rev"]
        st, _, upd = http_json(port, "PATCH", f"/api/v1/items/{key}", {"title": "自动化验证单据-已更新", "expected_rev": rev1})
        assert st == 200 and upd["item"]["rev"] == rev1 + 1
        st, _, conflict_res = http_json(port, "PATCH", f"/api/v1/items/{key}", {"title": "过期写入", "expected_rev": rev1})
        assert st == 409 and conflict_res["error"] == "rev_conflict"

        # 4. 严格模式打回原因校验
        st, _, _ = http_json(port, "PATCH", f"/api/v1/projects/{pid}", {"strict_mode": True})
        assert st == 200
        st, _, err_res = http_json(port, "POST", f"/api/v1/items/{key}/transition", {"action": "reject", "reason": ""})
        assert st == 400 and err_res["error"] == "validation_error"
        st, _, rej_ok = http_json(port, "POST", f"/api/v1/items/{key}/transition", {"action": "reject", "reason": "缺少边缘用例"})
        assert st == 200 and rej_ok["item"]["state"] == "rejected"

        # 5. SHA-256 附件去重上传与回读
        b64_png = base64.b64encode(_TINY_PNG).decode("ascii")
        st1, _, att1 = http_json(port, "POST", f"/api/v1/items/{key}/attachments", {"filename": "shot1.png", "mime_type": "image/png", "data_base64": b64_png})
        st2, _, att2 = http_json(port, "POST", f"/api/v1/items/{key}/attachments", {"filename": "shot2.png", "mime_type": "image/png", "data_base64": b64_png})
        assert st1 == 201 and st2 == 201 and att1["attachment"]["sha256"] == att2["attachment"]["sha256"]
        with _OPENER.open(f"http://127.0.0.1:{port}{att1['attachment']['url']}", timeout=5.0) as resp:
            assert resp.status == 200 and resp.read() == _TINY_PNG

        # 6. SSE 实时广播校验
        sse_events: list[str] = []
        ready_ev = threading.Event()

        def _sse_reader() -> None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=6.0)
            conn.request("GET", "/api/v1/events")
            resp = conn.getresponse()
            assert resp.status == 200
            while True:
                line = resp.fp.readline().decode("utf-8", errors="replace")
                if not line:
                    break
                sse_events.append(line.strip())
                if line.startswith("event: connected"):
                    ready_ev.set()
                if line.startswith("event: board_update"):
                    break
            conn.close()

        sse_th = threading.Thread(target=_sse_reader, daemon=True)
        sse_th.start()
        assert ready_ev.wait(timeout=3.0), "SSE connected 事件超时"
        http_json(port, "POST", f"/api/v1/items/{key}/comments", {"body_md": "触发 SSE 实时推送"})
        sse_th.join(timeout=3.0)
        assert any("event: board_update" in line for line in sse_events), f"未收到 SSE board_update：{sse_events}"

        # 7. AI Agent CLI 全流程闭环
        agent_tok = next(m["api_token"] for m in boot["members"] if m["kind"] == "agent")
        cli_created = run_cli(
            port,
            "--json",
            "create",
            "--title",
            "CLI 自动化流转任务",
            "--module",
            "工作项看板与多Agent协作",
            "--priority",
            "high",
            "--scope",
            "scripts/工作项看板/",
            token=agent_tok,
        )
        ckey = cli_created["item_key"]
        pulled = run_cli(port, "--json", "pull", ckey, token=agent_tok)
        assert pulled["module_context"]["name"] == "工作项看板与多Agent协作" and pulled["module_context"]["category"] == "团队协作与研发工具" and "18800" in pulled["module_context"]["redlines"]
        run_cli(port, "claim", ckey, "--branch", "feat/cli-test", token=agent_tok)
        run_cli(port, "submit-plan", ckey, "--plan", "1. 实现要点 2. 验证方案", token=agent_tok)
        run_cli(port, "approve-plan", ckey, token=agent_tok)
        run_cli(port, "heartbeat", ckey, "--note", "编码已完成", token=agent_tok)
        run_cli(port, "submit-qa", ckey, "--note", "自动化闭环通过", token=agent_tok)
        done_item = run_cli(port, "--json", "done", ckey, "--note", "验收闭环完成", token=agent_tok)
        assert done_item["state"] == "done"
        conflicts_out = run_cli(port, "--json", "conflicts", token=agent_tok)
        assert len(conflicts_out["conflict_pairs"]) >= 1

        # 8. 规范门禁校验：--dry-run 拦截、--desc-file/--label/--blocked-by/--comment/--check-spec、update、lint、--plan-file
        bad_proc = run_cli_raw(
            port,
            "--json",
            "create",
            "--title",
            "随便写个标题",
            "--desc",
            "太短了",
            "--dry-run",
            token=agent_tok,
        )
        assert bad_proc.returncode == 2, f"不合规草稿预期返回 2，实际 {bad_proc.returncode}"
        bad_payload = json.loads(bad_proc.stdout)
        assert bad_payload["dry_run"] is True and bad_payload["spec_lint"]["ok"] is False and len(bad_payload["spec_lint"]["errors"]) >= 3

        spec_body_file = tmp_dir / "spec_item.md"
        spec_body_file.write_text(
            "\n".join([
                "### 触发场景",
                "- 路由 / 模式：/play 9km 实战 · 21:9 680p 双画幅",
                "- 前置条件：编组 3 辆 M1A2 与 1 架 A-10C 固定翼协同",
                "",
                "### 复现步骤",
                "1. 在 21:9 画幅下选中 A-10C 并打开投弹指令预览。",
                "2. 将鼠标移至屏幕左右边缘区域并切换镜头俯仰角。",
                "",
                "### 实际表现",
                "固定翼贴地主范围圈在超宽屏边缘出现约 18px 水平偏移，右键拖拽预览时偶尔闪烁。",
                "",
                "### 预期表现",
                "21:9 与 16:9 画幅下贴地投弹圈坐标与落点完全重合，右键预览稳定无闪烁，不破坏原生高清标绘层。",
            ]),
            encoding="utf-8",
        )
        dry_ok = run_cli(
            port,
            "--json",
            "create",
            "--title",
            "[缺陷] 21:9画幅下固定翼投弹圈偏移",
            "--module",
            "操作交互与战术标绘",
            "--priority",
            "high",
            "--label",
            "缺陷修复,UI/UX,需评审",
            "--blocked-by",
            ckey,
            "--scope",
            "src/交互系统/指令与标绘层.mjs",
            "--desc-file",
            str(spec_body_file),
            "--comment",
            "排查线索：疑似投影矩阵宽高比未随 21:9 Hor+ 同步刷新。",
            "--dry-run",
            token=agent_tok,
        )
        assert dry_ok["spec_lint"]["ok"] is True and len(dry_ok["spec_lint"]["conflicts"]) >= 1

        spec_created = run_cli(
            port,
            "--json",
            "create",
            "--title",
            "[缺陷] 21:9画幅下固定翼投弹圈偏移",
            "--module",
            "操作交互与战术标绘",
            "--priority",
            "high",
            "--label",
            "缺陷修复,UI/UX,需评审",
            "--blocked-by",
            ckey,
            "--scope",
            "src/交互系统/指令与标绘层.mjs",
            "--desc-file",
            str(spec_body_file),
            "--comment",
            "排查线索：疑似投影矩阵宽高比未随 21:9 Hor+ 同步刷新。",
            "--check-spec",
            token=agent_tok,
        )
        skey = spec_created["item_key"]
        assert {lb["name"] for lb in spec_created["labels"]} == {"缺陷修复", "UI/UX", "需评审"}
        assert spec_created["blocked_by"] == [ckey]
        assert any("排查线索" in c["body_md"] for c in spec_created["comments"])

        # update 增量摘除「需评审」并跑单条 lint 通过
        updated_item = run_cli(
            port,
            "--json",
            "update",
            skey,
            "--remove-label",
            "需评审",
            "--add-scope",
            "src/交互系统/契约与仲裁.mjs",
            "--check-spec",
            token=agent_tok,
        )
        assert {lb["name"] for lb in updated_item["labels"]} == {"缺陷修复", "UI/UX"}
        assert "src/交互系统/契约与仲裁.mjs" in updated_item["scope_paths"]
        lint_one = run_cli(port, "--json", "lint", skey, token=agent_tok)
        assert lint_one["ok"] is True

        # submit-plan --plan-file 校验
        plan_file = tmp_dir / "plan.md"
        plan_file.write_text("### 1. 定位\n统一读取视口尺寸。\n### 2. 验证\n跑通交互规则单测。", encoding="utf-8")
        plan_res = run_cli(port, "--json", "submit-plan", skey, "--plan-file", str(plan_file), token=agent_tok)
        assert plan_res["state"] == "plan_review" and "统一读取视口尺寸" in plan_res["plan_md"]

        # lint --all 汇总返回结构校验（含前文构造的简易卡，应识别出需整改条数）
        lint_all_proc = run_cli_raw(port, "--json", "lint", "--all", token=agent_tok)
        assert lint_all_proc.returncode in (0, 2)
        lint_all_data = json.loads(lint_all_proc.stdout)
        assert lint_all_data["passed"] >= 1 and lint_all_data["failed"] >= 1
    finally:
        server.broker.close_all()
        server.shutdown()
        server.server_close()


def verify_lan_pin_mode(tmp_dir: Path) -> None:
    port = pick_free_port()
    store = BoardStore(tmp_dir / "数据" / "workboard.db", seed_workspace=True)
    server = WorkboardServer(
        ("127.0.0.1", port),
        store=store,
        data_dir=tmp_dir,
        host="127.0.0.1",
        auth_mode="lan_pin",
        control_token="test-ctrl-lan",
        session_secret=load_or_create_session_secret(tmp_dir),
    )
    th = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    try:
        # 未登录应返回 401
        st, _, _ = http_json(port, "GET", "/api/v1/bootstrap")
        assert st == 401, f"lan_pin 模式未登录预期 401，实际 {st}"

        # 查人类成员并用默认 PIN 1234 登录获取 Cookie
        st, _, auth_st = http_json(port, "GET", "/api/v1/auth/state")
        assert st == 200 and auth_st["auth_mode"] == "lan_pin"
        human_id = auth_st["human_members"][0]["id"]

        st_bad, _, _ = http_json(port, "POST", "/api/v1/auth/login", {"member_id": human_id, "pin": "0000"})
        assert st_bad == 401

        st_ok, hdrs, login_res = http_json(port, "POST", "/api/v1/auth/login", {"member_id": human_id, "pin": "1234"})
        assert st_ok == 200 and login_res["ok"]
        set_cookie = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
        cookie_pair = set_cookie.split(";", 1)[0]
        assert cookie_pair.startswith("lb_session=")

        # 凭 Cookie 访问成功并拿到 Agent Token
        st, _, boot = http_json(port, "GET", "/api/v1/bootstrap", headers={"Cookie": cookie_pair})
        assert st == 200 and boot["current_member"]["id"] == human_id
        agent_tok = next(m["api_token"] for m in boot["members"] if m["kind"] == "agent")

        # 凭 X-Board-Token 直接访问成功
        st_tok, _, boot_agent = http_json(port, "GET", "/api/v1/bootstrap", headers={"X-Board-Token": agent_tok})
        assert st_tok == 200 and boot_agent["current_member"]["kind"] == "agent"
    finally:
        server.broker.close_all()
        server.shutdown()
        server.server_close()


def verify_quick_launcher(tmp_dir: Path) -> None:
    """校验根目录 启动工作任务看板.cmd 编码换行、透传、后台接管、前台保护与优雅停服释放端口。"""
    cmd_path = ROOT / "启动工作任务看板.cmd"
    assert cmd_path.is_file(), f"缺少快捷启动脚本：{cmd_path}"
    raw = cmd_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "启动工作任务看板.cmd 不应包含 UTF-8 BOM"
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), "启动工作任务看板.cmd 必须统一使用 CRLF 换行"
    text = raw.decode("utf-8")
    assert "chcp 65001" in text and "serve --open --panel" in text and "工作任务看板" in text

    port = pick_free_port()
    env = {**dict(os.environ), "WORKBOARD_NO_PAUSE": "1"}
    base_cmd = ["cmd.exe", "/c", str(cmd_path)]

    # 1. 未启动时 status 返回非 0 且 running=false
    p_st0 = subprocess.run(
        [*base_cmd, "status", "--port", str(port), "--data-dir", str(tmp_dir), "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert p_st0.returncode == 1 and json.loads(p_st0.stdout)["running"] is False

    # 2. 通过 .cmd 启动后台实例 -> /healthz 报告 run_mode=background
    p_start = subprocess.run(
        [*base_cmd, "start", "--port", str(port), "--data-dir", str(tmp_dir), "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert p_start.returncode == 0, f"start 失败：{p_start.stderr or p_start.stdout}"
    st, _, hz_bg = http_json(port, "GET", "/healthz")
    assert st == 200 and hz_bg["run_mode"] == "background"
    bg_pid = hz_bg["pid"]

    fg_proc = None
    try:
        # 3. 前台启动应平滑接管后台实例，切换为 run_mode=foreground 且 PID 变更
        fg_proc = subprocess.Popen(
            [*base_cmd, "serve", "--panel", "--port", str(port), "--data-dir", str(tmp_dir)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        deadline = time.monotonic() + 10.0
        hz_fg = None
        while time.monotonic() < deadline:
            try:
                st2, _, hz = http_json(port, "GET", "/healthz")
                if st2 == 200 and hz.get("run_mode") == "foreground" and hz.get("pid") != bg_pid:
                    hz_fg = hz
                    break
            except Exception:
                pass
            time.sleep(0.15)
        assert hz_fg is not None, "前台启动未在 10 秒内接管后台实例"
        fg_pid = hz_fg["pid"]

        # 4. 重复前台启动应保护已有前台窗口，不抢占 PID 且立即返回 0
        p_dup = subprocess.run(
            [*base_cmd, "serve", "--port", str(port), "--data-dir", str(tmp_dir)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert p_dup.returncode == 0 and "复用" in p_dup.stdout
        st3, _, hz_after = http_json(port, "GET", "/healthz")
        assert st3 == 200 and hz_after["pid"] == fg_pid

        # 5. 通过 .cmd stop 优雅停止，验证端口释放且状态文件已清理
        p_stop = subprocess.run(
            [*base_cmd, "stop", "--port", str(port), "--data-dir", str(tmp_dir)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert p_stop.returncode == 0 and json.loads(p_stop.stdout).get("ok") is True
        fg_proc.wait(timeout=8.0)
        assert not (tmp_dir / "运行状态" / f"server-{port}.json").exists()
    finally:
        if fg_proc and fg_proc.poll() is None:
            fg_proc.terminate()
            try:
                fg_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                fg_proc.kill()


def verify_parent_child_and_migration(tmp_dir: Path) -> None:
    """校验存量库 parent_id 幂等迁移、2 层父子工作项全链路、防成环/防误报/防误结单与 pull 父兄上下文。"""
    import sqlite3

    # 1. 模拟不含 parent_id 列的旧版 SQLite 数据库，验证启动自动幂等迁移且旧数据零丢失
    legacy_db = tmp_dir / "legacy" / "workboard.db"
    legacy_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(legacy_db))
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', strict_mode INTEGER NOT NULL DEFAULT 0,
            next_seq INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, name TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '', scope_desc TEXT NOT NULL DEFAULT '',
            capabilities TEXT NOT NULL DEFAULT '', redlines TEXT NOT NULL DEFAULT '',
            entry_paths_json TEXT NOT NULL DEFAULT '[]', verify_cmd TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#6366f1', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE work_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq_num INTEGER NOT NULL,
            item_key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, description_md TEXT NOT NULL DEFAULT '',
            plan_md TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'todo', priority TEXT NOT NULL DEFAULT 'medium',
            module_id INTEGER, owner_id INTEGER, agent_id INTEGER, agent_host TEXT NOT NULL DEFAULT '',
            agent_branch TEXT NOT NULL DEFAULT '', agent_heartbeat_at TEXT NOT NULL DEFAULT '',
            scope_paths_json TEXT NOT NULL DEFAULT '[]', blocked_by_json TEXT NOT NULL DEFAULT '[]',
            sort_order REAL NOT NULL DEFAULT 1000.0, rev INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO projects(key, name, created_at, updated_at) VALUES ('OLD', '旧库迁移校验', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO work_items(project_id, seq_num, item_key, title, created_at, updated_at)
        VALUES (1, 1, 'OLD-1', '历史存量卡片', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    migrated_store = BoardStore(legacy_db, seed_workspace=False)
    migrated_detail = migrated_store.get_item_detail("OLD-1")
    assert migrated_detail["parent_id"] is None and migrated_detail["sub_total"] == 0 and migrated_detail["title"] == "历史存量卡片"

    # 2. 启动隔离服务，端到端校验父子工作项（以 F-22 跨模块拆单为样本）
    port = pick_free_port()
    data_dir = tmp_dir / "pc_srv"
    store = BoardStore(data_dir / "数据" / "workboard.db", seed_workspace=True)
    server = WorkboardServer(
        ("127.0.0.1", port),
        store=store,
        data_dir=data_dir,
        host="127.0.0.1",
        auth_mode="local_single",
        control_token="test-ctrl-pc",
        session_secret=load_or_create_session_secret(data_dir),
    )
    th = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    try:
        st, _, boot = http_json(port, "GET", "/api/v1/bootstrap")
        assert st == 200
        pid = boot["projects"][0]["id"]
        agent_tok = next(m["api_token"] for m in boot["members"] if m["kind"] == "agent")

        epic_md = tmp_dir / "f22_epic.md"
        epic_md.write_text(
            "\n".join([
                "### 背景与目标",
                "完成 F-22 固定翼从模型编译、数值基底、音效特效到操作交互的全链路跨模块实装与联调验收。",
                "",
                "### 核心变更",
                "- 统一定义 F-22 固定翼机动作战契约并拆分至各实战模块并行推进。",
                "- 所有子模块验收后在 8800/play 与 /air-lab 完成整机联调。",
                "",
                "### 验收标准",
                "- [ ] 全部子工作项完成验收，F-22 在 16:9 与 21:9 680p 基线下运行稳定。",
            ]),
            encoding="utf-8",
        )
        sub_md = tmp_dir / "f22_sub.md"
        sub_md.write_text(
            "\n".join([
                "### 背景与目标",
                "补齐 F-22 固定翼在本模块下的核心参数与交互能力，严格遵守模块架构红线。",
                "",
                "### 核心变更",
                "- 接入 F-22 型号数据并在对应模块内完成运行闭环。",
                "- 保持与同父单兄弟模块接口字段完全对齐。",
                "",
                "### 验收标准",
                "- [ ] 模块标准验证命令与全仓结构门禁均通过。",
            ]),
            encoding="utf-8",
        )

        # 2a. 创建统筹父单（--epic 允许暂不填业务源码 --scope 并通过 --check-spec）
        epic = run_cli(
            port,
            "--json",
            "create",
            "--title",
            "[需求] F-22固定翼全链路实装与验收",
            "--module",
            "载具战斗与数值基底",
            "--priority",
            "high",
            "--label",
            "功能需求",
            "--epic",
            "--desc-file",
            str(epic_md),
            "--check-spec",
            token=agent_tok,
        )
        epic_key = epic["item_key"]

        # 2b. 创建两个跨模块子单，其中 sub2 被 sub1 阻塞
        sub1 = run_cli(
            port,
            "--json",
            "create",
            "--title",
            "[需求] F-22数值基底与飞行弹药配置",
            "--parent",
            epic_key,
            "--module",
            "载具战斗与数值基底",
            "--label",
            "功能需求,数值/规则",
            "--scope",
            "src/程序化地图/固定翼底座.mjs",
            "--desc-file",
            str(sub_md),
            "--check-spec",
            token=agent_tok,
        )
        sub1_key = sub1["item_key"]
        sub2 = run_cli(
            port,
            "--json",
            "create",
            "--title",
            "[需求] F-22战术标绘与攻势交互接入",
            "--parent",
            epic_key,
            "--module",
            "操作交互与战术标绘",
            "--label",
            "功能需求,UI/UX",
            "--blocked-by",
            sub1_key,
            "--scope",
            "src/交互系统/指令与标绘层.mjs",
            "--desc-file",
            str(sub_md),
            "--check-spec",
            token=agent_tok,
        )
        sub2_key = sub2["item_key"]
        assert sub1["parent_key"] == epic_key and sub2["parent_key"] == epic_key
        assert sub2["blocked_open_count"] == 1

        # 2c. 校验 2 层结构限制与防环拦截
        st_grand, _, err_grand = http_json(port, "POST", f"/api/v1/projects/{pid}/items", {
            "title": "非法孙子单",
            "parent_key": sub1_key,
        })
        assert st_grand == 400 and "2 层结构" in err_grand["message"]

        st_self, _, _ = http_json(port, "PATCH", f"/api/v1/items/{epic_key}", {"parent_key": epic_key})
        assert st_self == 400

        st_demote, _, err_demote = http_json(port, "POST", f"/api/v1/projects/{pid}/items", {"title": "另一顶层单"})
        assert st_demote == 201
        other_top = err_demote["item"]["item_key"]
        st_nest, _, err_nest = http_json(port, "PATCH", f"/api/v1/items/{epic_key}", {"parent_key": other_top})
        assert st_nest == 400 and "已包含" in err_nest["message"]

        # 2d. 父单与子单同路径处于 in_progress 不应互相误报冲突
        st_p1, _, _ = http_json(port, "PATCH", f"/api/v1/items/{epic_key}", {"state": "in_progress", "scope_paths": ["src/程序化地图/固定翼底座.mjs"]})
        st_s1, _, _ = http_json(port, "PATCH", f"/api/v1/items/{sub1_key}", {"state": "in_progress"})
        assert st_p1 == 200 and st_s1 == 200
        st_lst, _, lst_data = http_json(port, "GET", f"/api/v1/projects/{pid}/items")
        assert st_lst == 200 and len(lst_data["conflict_pairs"]) == 0, f"父子同路径不应报假冲突：{lst_data['conflict_pairs']}"

        # 2e. CLI list --parent / --top-level 与 pull 父兄上下文及上下游阻塞
        tree_list = run_cli(port, "--json", "list", "--parent", epic_key, token=agent_tok)
        assert {x["item_key"] for x in tree_list["items"]} == {epic_key, sub1_key, sub2_key}
        top_list = run_cli(port, "--json", "list", "--top-level", token=agent_tok)
        assert sub1_key not in {x["item_key"] for x in top_list["items"]} and epic_key in {x["item_key"] for x in top_list["items"]}

        pull_sub1 = run_cli(port, "--json", "pull", sub1_key, token=agent_tok)
        assert pull_sub1["parent_detail"]["item_key"] == epic_key
        assert any(s["item_key"] == sub2_key for s in pull_sub1["siblings"])
        assert any(b["item_key"] == sub2_key for b in pull_sub1["blocks_details"])
        pull_sub1_txt = run_cli(port, "pull", sub1_key, token=agent_tok)
        assert "[所属父工作项总目标" in pull_sub1_txt and "[同父兄弟子项" in pull_sub1_txt and "阻塞下游" in pull_sub1_txt

        pull_epic_txt = run_cli(port, "pull", epic_key, token=agent_tok)
        assert "[子工作项清单 (0/2)]" in pull_epic_txt

        # 2f. 父单防误结单保护（未完成子项拦截 -> 全部子项 done 后放行；以及 --force 显式放行）
        st_block_done, _, err_done = http_json(port, "POST", f"/api/v1/items/{epic_key}/transition", {"action": "done"})
        assert st_block_done == 400 and err_done.get("reason_code") == "incomplete_children" and len(err_done.get("incomplete_children") or []) == 2

        run_cli(port, "done", sub1_key, "--note", "子单1通过", token=agent_tok)
        # sub1 完成后 sub2 的 blocked_open_count 应归零
        sub2_after = run_cli(port, "--json", "pull", sub2_key, token=agent_tok)
        assert sub2_after["blocked_open_count"] == 0
        run_cli(port, "done", sub2_key, "--note", "子单2通过", token=agent_tok)

        epic_done = run_cli(port, "--json", "done", epic_key, "--note", "全部子项验收完成", token=agent_tok)
        assert epic_done["state"] == "done" and epic_done["sub_done"] == 2 and epic_done["sub_total"] == 2

        # 2g. 验证 --force 强制结单 + update --parent "" 解绑 + --add-blocked-by / --remove-blocked-by + 删除父单保留子单
        sub_extra = run_cli(port, "--json", "create", "--title", "[任务] 挂在另一顶层单下的子项", "--parent", other_top, "--scope", "scripts/工作项看板/", token=agent_tok)
        ekey = sub_extra["item_key"]
        st_no_force, _, _ = http_json(port, "POST", f"/api/v1/items/{other_top}/transition", {"action": "done"})
        assert st_no_force == 400
        forced_done = run_cli(port, "--json", "done", other_top, "--force", "--note", "工程师确认强制收口", token=agent_tok)
        assert forced_done["state"] == "done"

        upd_blk = run_cli(port, "--json", "update", ekey, "--add-blocked-by", sub1_key, token=agent_tok)
        assert sub1_key in upd_blk["blocked_by"]
        upd_unblk = run_cli(port, "--json", "update", ekey, "--remove-blocked-by", sub1_key, "--parent", "", token=agent_tok)
        assert upd_unblk["blocked_by"] == [] and upd_unblk["parent_id"] is None

        # 重新挂回去再删父单，确认子单保留且自动转为顶层工作项
        run_cli(port, "update", ekey, "--parent", other_top, token=agent_tok)
        st_del, _, _ = http_json(port, "DELETE", f"/api/v1/items/{other_top}")
        assert st_del == 200
        surviving = run_cli(port, "--json", "pull", ekey, token=agent_tok)
        assert surviving["parent_id"] is None and surviving["item_key"] == ekey
    finally:
        server.broker.close_all()
        server.shutdown()
        server.server_close()


def verify_project_skills_and_frontend() -> None:
    """校验项目级工作项双技能（create / exec）与前端 5 类统一模板、父子层级/阻塞控件完整性。"""
    required_files = [
        (ROOT / ".agents/skills/workboard-item-create/SKILL.md", "name: workboard-item-create"),
        (ROOT / ".agents/skills/workboard-item-create/SKILL.md", "--parent"),
        (ROOT / ".agents/skills/workboard-item-create/references/工作项编写规范与模板.md", "### 5. 游戏资产（`[资产]`）"),
        (ROOT / ".agents/skills/workboard-item-exec/SKILL.md", "name: workboard-item-exec"),
        (ROOT / ".agents/skills/workboard-item-exec/SKILL.md", "同父兄弟子项"),
        (ROOT / ".agents/skills/workboard-item-exec/references/状态流转与协作规程.md", "plan_review"),
    ]
    for fp, marker in required_files:
        assert fp.is_file(), f"缺少项目级技能文件：{fp}"
        content = fp.read_text(encoding="utf-8")
        assert marker in content, f"技能文件缺少关键内容 {marker}：{fp}"

    app_js = (ROOT / "src/工作项看板/应用.mjs").read_text(encoding="utf-8")
    index_html = (ROOT / "src/工作项看板/index.html").read_text(encoding="utf-8")
    styles_css = (ROOT / "src/工作项看板/样式.css").read_text(encoding="utf-8")
    for marker in ("ITEM_SPEC_TEMPLATES", "[需求]", "[缺陷]", "[优化]", "[任务]", "[资产]", "workboard-item-exec", "sub-items-box", "d-parent", "d-blocker-add", "topLevelOnly", "bindAllUiSelects", "createBrandLogoSvg"):
        assert marker in app_js, f"看板前端缺少统一规范模板、父子层级或 V2.2 控件：{marker}"
    assert "/ui/令牌.css" in index_html and "/ui/组件.css" in index_html, "看板 index.html 必须同源引入 /ui/令牌.css 与 /ui/组件.css"
    assert "<select" not in index_html.lower() and "<select" not in app_js.lower(), "看板界面严禁使用浏览器原生 <select>，必须全部消费 .ui-select"
    assert "flex-shrink: 0;" in styles_css, "看板样式必须保留 .card flex-shrink: 0 防止多卡列压扁标题"


def main() -> int:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="workboard-test-single-") as d1:
        verify_local_single_mode(Path(d1))
    with tempfile.TemporaryDirectory(prefix="workboard-test-parent-") as d_pc:
        verify_parent_child_and_migration(Path(d_pc))
    with tempfile.TemporaryDirectory(prefix="workboard-test-lan-") as d2:
        verify_lan_pin_mode(Path(d2))
    with tempfile.TemporaryDirectory(prefix="workboard-test-launcher-") as d3:
        verify_quick_launcher(Path(d3))
    verify_project_skills_and_frontend()
    elapsed = round(time.monotonic() - t0, 2)
    print(f"[通过] 本地工作项看板自动化闭环验证全部通过（10 大项，含存量迁移/2层父子工作项/父兄上下文/防误结单/前置阻塞/单人免登/局域网PIN/Agent Token/乐观锁409/路径冲突/SHA256附件/SSE/CLI/快捷启动脚本/统一编写规范与项目级Skills，耗时 {elapsed}s）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
