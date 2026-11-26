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
        # 1. /healthz 与静态资源
        st, _, hz = http_json(port, "GET", "/healthz")
        assert st == 200 and hz["service"] == "scavenger-workboard" and hz["port"] == port
        for static_path in ("/", "/styles.css", "/app.mjs", "/favicon.svg"):
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


def verify_project_skills_and_frontend() -> None:
    """校验项目级工作项双技能（create / exec）与前端 5 类统一模板资产完整性。"""
    required_files = [
        (ROOT / ".agents/skills/workboard-item-create/SKILL.md", "name: workboard-item-create"),
        (ROOT / ".agents/skills/workboard-item-create/references/工作项编写规范与模板.md", "### 5. 游戏资产（`[资产]`）"),
        (ROOT / ".agents/skills/workboard-item-exec/SKILL.md", "name: workboard-item-exec"),
        (ROOT / ".agents/skills/workboard-item-exec/references/状态流转与协作规程.md", "plan_review"),
    ]
    for fp, marker in required_files:
        assert fp.is_file(), f"缺少项目级技能文件：{fp}"
        content = fp.read_text(encoding="utf-8")
        assert marker in content, f"技能文件缺少关键内容 {marker}：{fp}"

    app_js = (ROOT / "src/工作项看板/应用.mjs").read_text(encoding="utf-8")
    for marker in ("ITEM_SPEC_TEMPLATES", "[需求]", "[缺陷]", "[优化]", "[任务]", "[资产]", "workboard-item-exec"):
        assert marker in app_js, f"看板前端缺少统一规范模板或技能引导：{marker}"


def main() -> int:
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="workboard-test-single-") as d1:
        verify_local_single_mode(Path(d1))
    with tempfile.TemporaryDirectory(prefix="workboard-test-lan-") as d2:
        verify_lan_pin_mode(Path(d2))
    with tempfile.TemporaryDirectory(prefix="workboard-test-launcher-") as d3:
        verify_quick_launcher(Path(d3))
    verify_project_skills_and_frontend()
    elapsed = round(time.monotonic() - t0, 2)
    print(f"[通过] 本地工作项看板自动化闭环验证全部通过（9 大项，含单人免登/局域网PIN/Agent Token/乐观锁409/路径冲突/SHA256附件/SSE/CLI/快捷启动脚本/统一编写规范与项目级Skills，耗时 {elapsed}s）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
