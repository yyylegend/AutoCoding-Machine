"""CompletionGate V2 seam 测试。

覆盖 docs/plans/2026-09-01-completion-verification-v2.md 的验收标准。

观察原则（按计划要求）：只通过公共 seam 断言——
Gate 的 CompletionDecision、MachineLoop 的 result dict、
StreamingAdapter 的 last_streamed、SessionStore.load() 的 JSONL 内容。
不断言 Gate 的私有字段布局。
"""

import io

from rich.console import Console

from src.engine import AgentResponse, BudgetPolicy, CancellationToken, ToolCall
from src.engine.session_store import SessionStore, sessions_dir_for
from src.profiles.coding import llm_adapter
from src.profiles.coding.cli_ui import THEME
from src.profiles.coding.completion_gate import CompletionGate
from src.profiles.coding.llm_adapter import StreamingAdapter
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools import CodingTools
from src.runtime.factory import create_coding_runtime


# ================================================================
# 公共小工具
# ================================================================

def make_gate(tmp_path) -> CompletionGate:
    """构造一个挂在 tmp workspace 沙箱上的 Gate。"""
    return CompletionGate(WorkspaceSandbox(tmp_path))


def write_and_track(gate: CompletionGate, tmp_path, rel: str, content: str) -> None:
    """模拟一次"写工具成功执行"：拍基线 → 真写文件 → 记版本。

    和真实链路（pre_tool 快照 → 工具执行 → post_tool 记版本）一一对应。
    """
    gate.before_tool("write_file", {"path": rel})
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    gate.after_tool("write_file", False, {"path": rel})


def validate(gate: CompletionGate, command: str = "pytest -q",
             exit_code: int = 0, tool_name: str = "run_test") -> None:
    """模拟一次验证命令执行结果（走 post_tool 的 metadata 契约）。"""
    gate.after_tool(tool_name, False, {"exit_code": exit_code, "command": command})


def make_model(steps, calls):
    """按剧本回放的假模型：每次调用弹出一个 AgentResponse。

    steps 里可以放可调用对象（先执行副作用再返回响应），
    用来在"模型说话前"注入验证证据这类事件。
    """
    def model_fn(messages):
        calls.append(list(messages))
        step = steps.pop(0)
        return step() if callable(step) else step

    return model_fn


def make_runtime(tmp_path, steps, calls, store=None, **kwargs):
    """构造一个可跑通完整循环的 Coding Runtime（上下文全部 mock 掉）。"""
    from unittest.mock import Mock

    context_manager = Mock()
    context_manager.maybe_compact.side_effect = lambda messages, force=False: messages
    context_selector = Mock()
    context_selector.select.side_effect = lambda messages: messages

    runtime = create_coding_runtime(
        workspace=tmp_path,
        model_fn=make_model(steps, calls),
        session_store=store,
        context_manager=context_manager,
        context_selector=context_selector,
        **kwargs,
    )
    return runtime


def validated_reply(hooks_holder, text):
    """一个 step：先向 hooks 发"测试通过"事件，再返回最终回答。

    hooks_holder 是个 dict，runtime 建好后把 hooks 塞进去。
    走 post_tool Hook 是公共 seam，和真实 run_test 执行后的证据流一致。
    """
    def step():
        hooks_holder["hooks"].fire(
            "post_tool", tool_name="run_test", error=False,
            result_metadata={"exit_code": 0, "command": "pytest -q"},
        )
        return AgentResponse(content=text, done=True)

    return step


def write_step(rel: str, content: str) -> AgentResponse:
    """一个 step：模型发起一次 write_file 工具调用。"""
    return AgentResponse(tool_calls=[
        ToolCall(id="t-" + rel.replace("/", "-"),
                 name="write_file",
                 arguments={"path": rel, "content": content}),
    ])


# ================================================================
# 一、文件净变化（AC-1 / AC-2 / FR-7 / FR-8）
# ================================================================

def test_temporary_file_created_then_deleted_is_not_a_net_change(tmp_path):
    """AC-1：临时文件建了又删，不构成净变化，不要求验证。"""
    gate = make_gate(tmp_path)

    write_and_track(gate, tmp_path, "_tmp_dump.py", "print('临时')")
    (tmp_path / "_tmp_dump.py").unlink()  # 脚本自删

    decision = gate.evaluate("任务完成")

    assert decision.action == "accept"
    assert decision.changed_paths == ()
    assert "测试" not in decision.reason  # 不需要验证


def test_file_restored_to_baseline_is_not_a_net_change(tmp_path):
    """AC-2：文件改了又改回原样，不构成净变化。"""
    gate = make_gate(tmp_path)
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("original", encoding="utf-8")

    gate.before_tool("edit_file", {"path": "src/a.py"})
    target.write_text("changed", encoding="utf-8")
    gate.after_tool("edit_file", False, {"path": "src/a.py"})
    target.write_text("original", encoding="utf-8")  # 恢复原内容

    decision = gate.evaluate("改完又还原了")

    assert decision.action == "accept"
    assert decision.changed_paths == ()


def test_out_of_workspace_path_is_ignored_by_gate(tmp_path):
    """NFR-3：越界路径 Gate 不读、不跟踪，也不崩。"""
    gate = make_gate(tmp_path)
    gate.before_tool("write_file", {"path": "../../etc/passwd"})
    gate.after_tool("write_file", False, {"path": "../../etc/passwd"})

    decision = gate.evaluate("完成")
    assert decision.action == "accept"


# ================================================================
# 二、验证证据（AC-3 ~ AC-6 / FR-11 ~ FR-17 / EC-5）
# ================================================================

def test_real_code_change_requires_fresh_verification(tmp_path):
    """AC-3：真实代码有净变化但没验证 → 退回验证；再不验证 → 交付未验证回答。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "print('v1')\n")

    first = gate.evaluate("我改好了 a.py")

    assert first.action == "continue"
    assert "src/a.py" in first.continuation_message

    # 模型继续瞎说完成（还是没跑测试）
    second = gate.evaluate("真的完成了")

    assert second.action == "fail"
    # 原候选回答不能丢，且带未验证标记（FR-25）
    assert "我改好了 a.py" in second.final_response
    assert "未验证" in second.final_response


def test_test_before_last_real_change_is_stale(tmp_path):
    """AC-5 前半 / FR-15：先跑测试后改代码，测试过期，仍要求验证。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate)  # 测试在版本 1 后通过

    # 之后又改了一次（版本 2）
    write_and_track(gate, tmp_path, "src/a.py", "v2\n")

    decision = gate.evaluate("完成")
    assert decision.action == "continue"  # 版本 1 的证据罩不住版本 2


def test_change_after_successful_test_invalidates_old_evidence(tmp_path):
    """AC-5：验证后再次修改真实代码，旧验证自动失效。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate)

    first = gate.evaluate("完成")
    assert first.action == "accept"  # 有新鲜证据，放行

    write_and_track(gate, tmp_path, "src/a.py", "v2\n")
    second = gate.evaluate("又改了一版")
    assert second.action == "continue"  # 旧证据失效


def test_temporary_write_after_validation_does_not_invalidate_evidence(tmp_path):
    """AC-6 / FR-17：验证后只创建又删除临时文件，原验证仍有效。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate)

    write_and_track(gate, tmp_path, "_tmp_dump.py", "临时")
    (tmp_path / "_tmp_dump.py").unlink()

    decision = gate.evaluate("完成")
    assert decision.action == "accept"


def test_failed_test_does_not_verify_change(tmp_path):
    """FR-12：run_test 退出码非 0 不算验证。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate, exit_code=1)

    assert gate.evaluate("完成").action == "continue"


def test_recognized_run_bash_command_verifies_change(tmp_path):
    """FR-13：run_bash 里 token 级命中测试/检查词且退出码 0，算验证。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate, command="uv run pytest -q", tool_name="run_bash")

    assert gate.evaluate("完成").action == "accept"


def test_unrelated_run_bash_command_does_not_verify(tmp_path):
    """EC-5：git status / pwd / ls 这类命令退出码 0 也不算验证。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate, command="git status", tool_name="run_bash")

    assert gate.evaluate("完成").action == "continue"


def test_git_checkout_does_not_match_check_token(tmp_path):
    """token 级匹配防误伤：checkout 不等于 check。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate, command="git checkout main", tool_name="run_bash")

    assert gate.evaluate("完成").action == "continue"


def test_error_result_never_counts_as_validation(tmp_path):
    """FR-12：工具本身报错（error=True）时，即使 exit_code 0 也不算。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    gate.after_tool("run_test", True, {"exit_code": 0, "command": "pytest -q"})

    assert gate.evaluate("完成").action == "continue"


def test_doc_only_change_skips_code_verification(tmp_path):
    """AC-16 / FR-10：只有 .md 变化时不要求代码测试，footer 如实标记。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "docs/notes.md", "# 笔记\n")

    decision = gate.evaluate("文档写好了")

    assert decision.action == "accept"
    assert "文档" in decision.final_response


def test_start_task_clears_previous_evidence(tmp_path):
    """EC-12 / FR-1：新任务开始时清空上一任务的全部证据。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/old.py", "x\n")

    assert gate.evaluate("上一任务").action == "continue"

    gate.start_task()
    assert gate.evaluate("全新任务").action == "accept"
    assert gate.take_pending_final() is None


def test_should_publish_stream_reflects_gate_state(tmp_path):
    """FR-27/28：有未验证修改时缓冲，验证后恢复展示。"""
    gate = make_gate(tmp_path)

    assert gate.should_publish_stream() is True  # 干净任务

    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    assert gate.should_publish_stream() is False  # 有未验证修改

    validate(gate)
    assert gate.should_publish_stream() is True  # 证据新鲜


# ================================================================
# 三、候选回答状态机（AC-4 / AC-7 / AC-8 / FR-19 ~ FR-24）
# ================================================================

def test_pending_candidate_is_reused_after_verification_only_continuation(tmp_path):
    """AC-4/AC-7 / FR-23：验证通过后复用原候选回答 + 验证 footer，
    模型的验证回执不得顶替实质回答。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")

    first = gate.evaluate("原讲解：a.py 现在打印 v1。")
    assert first.action == "continue"

    validate(gate)  # 只发生了验证，没有新的真实修改

    receipt = gate.evaluate("验证通过，任务完成。")
    assert receipt.action == "accept"
    assert receipt.final_response.startswith("原讲解")
    assert "已验证" in receipt.final_response
    assert "验证通过，任务完成" not in receipt.final_response


def test_pending_candidate_expires_after_new_real_change(tmp_path):
    """AC-8 / FR-24：验证期间又发生新的真实修改，旧候选回答过期，
    后续以新候选为准。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")

    first = gate.evaluate("v1 讲解")
    assert first.action == "continue"

    # 新的真实修改 + 新验证
    write_and_track(gate, tmp_path, "src/a.py", "v2\n")
    validate(gate)

    second = gate.evaluate("v2 回执")
    assert second.action == "accept"
    assert second.final_response == "v2 回执"  # 不复用过期的 v1 讲解


def test_candidate_response_bound_to_current_version(tmp_path):
    """FR-19：candidate 与有效修改版本绑定（通过 take_pending_final 行为观察）。"""
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")

    gate.evaluate("候选回答")
    pending = gate.take_pending_final()

    assert pending is not None
    assert "候选回答" in pending
    assert "未验证" in pending


# ================================================================
# 四、read_file 分页（AC-13 / AC-14 / FR-32 ~ FR-36）
# ================================================================

def _read(tmp_path, arguments):
    tools = CodingTools(tmp_path, max_output_chars=50_000)
    return tools.execute(ToolCall(id="r1", name="read_file", arguments=arguments))


def test_read_file_returns_requested_line_range(tmp_path):
    """AC-13：按行范围读取，带真实行号，附续读建议。"""
    target = tmp_path / "big.py"
    target.write_text(
        "\n".join("line %d" % i for i in range(1, 501)), encoding="utf-8"
    )

    result = _read(tmp_path, {"path": "big.py", "start_line": 200, "end_line": 260})

    assert result.error is False
    assert "200| line 200" in result.content
    assert "260| line 260" in result.content
    assert "199|" not in result.content
    assert "261|" not in result.content
    assert result.metadata["total_lines"] == 500
    # 未读完时给下一段建议
    assert "start_line=261" in result.content


def test_read_file_start_line_only_reads_to_end(tmp_path):
    """FR-34：只传 start_line 时读到文件末尾。"""
    target = tmp_path / "big.py"
    target.write_text("\n".join("l%d" % i for i in range(1, 11)), encoding="utf-8")

    result = _read(tmp_path, {"path": "big.py", "start_line": 8})

    assert result.error is False
    assert "8| l8" in result.content
    assert "10| l10" in result.content
    assert "start_line=" not in result.content  # 已读完，无续读建议


def test_read_file_rejects_invalid_line_range(tmp_path):
    """AC-14 / FR-35：非法分页参数返回 invalid_args，不读文件。"""
    target = tmp_path / "a.txt"
    target.write_text("content", encoding="utf-8")

    for arguments in (
        {"path": "a.txt", "start_line": 0},
        {"path": "a.txt", "start_line": -3},
        {"path": "a.txt", "start_line": 5, "end_line": 2},
        {"path": "a.txt", "start_line": "abc"},
        {"path": "a.txt", "start_line": 1.5},
    ):
        result = _read(tmp_path, arguments)
        assert result.error is True, "参数 %r 应被拒绝" % (arguments,)
        assert result.error_type == "invalid_args"


def test_read_file_without_pagination_keeps_old_behavior(tmp_path):
    """FR-33：不传分页参数时行为与旧版完全一致（原样文本，无行号）。"""
    target = tmp_path / "a.txt"
    target.write_text("第一行\n第二行\n", encoding="utf-8")

    result = _read(tmp_path, {"path": "a.txt"})

    assert result.error is False
    assert result.content == "第一行\n第二行\n"
    assert "1| " not in result.content


# ================================================================
# 五、MachineLoop 全链路（AC-9 / AC-10 / AC-11 / AC-15 / EC-11）
# ================================================================

def test_committed_final_response_is_displayed_once(tmp_path):
    """AC-4/AC-7 全链路：验证 continuation 后，原讲解 + footer 只提交一次，
    验证回执不落盘。"""
    store = SessionStore(sessions_dir_for(tmp_path), "s1")
    hooks_holder = {}
    calls = []
    steps = [
        write_step("src/a.py", "print('v1')\n"),
        AgentResponse(content="原讲解：a.py 现在打印 v1。", done=True),
        validated_reply(hooks_holder, "验证完成。"),
    ]
    runtime = make_runtime(tmp_path, steps, calls, store=store, auto_approve=True)
    hooks_holder["hooks"] = runtime.hooks

    result = runtime.run(
        [{"role": "user", "content": "改 a.py"}], CancellationToken()
    )

    assert result["status"] == "success"
    assert result["reply"].startswith("原讲解")
    assert "已验证" in result["reply"]
    assert "验证完成" not in result["reply"]

    # JSONL 里"原讲解"只出现一次，且没有内部验证提示
    committed = [m for m in store.load()
                 if m.get("role") == "assistant" and "原讲解" in str(m.get("content"))]
    assert len(committed) == 1
    assert not any("[完成验证]" in str(m.get("content")) for m in store.load())


def test_synthetic_verification_nudge_is_not_persisted(tmp_path):
    """AC-11 / FR-22：验证提示只存在于运行时消息，绝不写进 Session JSONL。"""
    store = SessionStore(sessions_dir_for(tmp_path), "s1")
    calls = []
    steps = [
        write_step("src/a.py", "v1\n"),
        AgentResponse(content="候选讲解", done=True),      # → continue，插入 nudge
        AgentResponse(content="还是完成了", done=True),    # 仍无证据 → fail
    ]
    runtime = make_runtime(tmp_path, steps, calls, store=store, auto_approve=True)

    result = runtime.run(
        [{"role": "user", "content": "改 a.py"}], CancellationToken()
    )

    assert result["status"] == "failed"
    assert result["error"] == "verification_required"
    assert "候选讲解" in result["reply"]
    assert "未验证" in result["reply"]

    # nudge 确实送到了模型面前（第三次调用能看到）
    third_call = "\n".join(str(m.get("content")) for m in calls[2])
    assert "[完成验证]" in third_call

    # 但 JSONL 里干干净净：既没有 nudge，也没有被拒绝的候选 assistant 消息
    for message in store.load():
        assert "[完成验证]" not in str(message.get("content"))
    candidates = [m for m in store.load()
                  if m.get("role") == "assistant" and "候选讲解" in str(m.get("content"))]
    assert len(candidates) == 1  # 只有最终交付那一次


def test_iteration_limit_preserves_pending_candidate(tmp_path):
    """AC-10 / FR-26：轮数耗尽时交付 pending candidate + 未验证 footer，
    不许只甩一个 max_turns。"""
    calls = []
    steps = [
        write_step("src/a.py", "v1\n"),
        AgentResponse(content="实质回答：改好了。", done=True),  # → continue
    ]
    runtime = make_runtime(
        tmp_path, steps, calls, auto_approve=True,
        budget=BudgetPolicy(max_turns=2),
    )

    result = runtime.run(
        [{"role": "user", "content": "改 a.py"}], CancellationToken()
    )

    assert result["status"] == "failed"
    assert result["error"] == "max_turns"
    assert "实质回答" in result["reply"]
    assert "未验证" in result["reply"]


def test_denied_verification_delivers_candidate_once(tmp_path):
    """AC-9 / EC-7：用户拒绝验证命令（工具报错），最终以未验证状态
    交付一次原候选回答。"""
    store = SessionStore(sessions_dir_for(tmp_path), "s1")
    calls = []
    steps = [
        write_step("src/a.py", "v1\n"),
        AgentResponse(content="候选讲解", done=True),   # → continue
        AgentResponse(content="再次完成", done=True),   # 仍无证据 → fail
    ]
    runtime = make_runtime(tmp_path, steps, calls, store=store, auto_approve=True)

    result = runtime.run(
        [{"role": "user", "content": "改 a.py"}], CancellationToken()
    )

    assert result["status"] == "failed"
    assert result["error"] == "verification_required"
    assert "候选讲解" in result["reply"]
    assert "未验证" in result["reply"]


def test_runtime_resume_preserves_pending_candidate_and_evidence(tmp_path):
    """EC-11：ASK 权限暂停后 resume，基线快照、pending candidate、
    验证证据都必须保留。"""
    store = SessionStore(sessions_dir_for(tmp_path), "s1")
    hooks_holder = {}
    calls = []
    steps = [
        write_step("src/a.py", "v1\n"),                      # ASK → 暂停
        AgentResponse(content="候选讲解", done=True),        # resume 后 → continue
        validated_reply(hooks_holder, "验证完成。"),         # 证据 + 回执 → accept
    ]
    runtime = make_runtime(tmp_path, steps, calls, store=store, auto_approve=False)
    hooks_holder["hooks"] = runtime.hooks
    tools = runtime.tools

    # 第一次 run：write_file 需要确认，循环挂起
    first = runtime.run(
        [{"role": "user", "content": "改 a.py"}], CancellationToken()
    )
    assert first["status"] == "permission_required"

    # 用户批准：真实执行工具，结果回填 + 落盘（和 CLI 的恢复逻辑一致，
    # post_tool 事件也要照发，Gate 靠它记修改版本）
    tool_result = tools.execute(first["pending_tool_call"])
    runtime.hooks.fire(
        "post_tool", tool_name=first["pending_tool_call"].name,
        tool_call_id=first["pending_tool_call"].id,
        error=tool_result.error, error_type=tool_result.error_type,
        result_content=tool_result.content,
        result_metadata=tool_result.metadata, duration_ms=0,
    )
    messages = first["messages"]
    messages.append(tool_result.to_message())
    store.append(tool_result.to_message())

    # resume：candidate → continue → 验证 → accept，全链路状态没丢
    second = runtime.resume(messages, CancellationToken())

    assert second["status"] == "success"
    assert second["reply"].startswith("候选讲解")
    assert "已验证" in second["reply"]


def test_original_session_scenario_tmp_dump_does_not_trigger_verification(tmp_path):
    """DoD-2/3：复现原 session 场景——模型为读长文件写 _tmp_dump.py、
    用自删脚本清理，最后给出讲解。Gate 不得要求验证，讲解直接生效。"""
    store = SessionStore(sessions_dir_for(tmp_path), "s1")
    calls = []
    steps = [
        # 模型为读长文件创建临时脚本
        write_step("_tmp_dump.py", "print(open('big.py').read())\n"),
        # 自删脚本：清掉临时脚本后把自己也删掉（原 session 的真实做法）
        write_step("_cleanup.py",
                   "import os\nos.remove('_tmp_dump.py')\nos.remove('_cleanup.py')\n"),
        AgentResponse(tool_calls=[ToolCall(
            id="t-clean", name="run_bash",
            arguments={"command": "python _cleanup.py"},
        )]),
        AgentResponse(content="讲解：摘要降级会生成确定性摘录……", done=True),
    ]
    runtime = make_runtime(tmp_path, steps, calls, store=store, auto_approve=True)

    result = runtime.run(
        [{"role": "user", "content": "讲解一下摘要降级"}], CancellationToken()
    )

    # 临时文件全部消失 → 无净变化 → 直接放行，不退回验证（V1 在这里误拦）
    assert result["status"] == "success"
    assert result["reply"].startswith("讲解")
    assert not (tmp_path / "_tmp_dump.py").exists()
    assert not any("[完成验证]" in str(m.get("content")) for m in store.load())


def test_machine_loop_without_gate_keeps_old_behavior(tmp_path):
    """AC-15 / NFR-1：不注入 CompletionGate 时，模型说 done 即成功，行为同旧版。"""
    calls = []
    steps = [AgentResponse(content="直接回答", done=True)]
    runtime = make_runtime(tmp_path, steps, calls, completion_gate=None)

    result = runtime.run(
        [{"role": "user", "content": "讲讲装饰器"}], CancellationToken()
    )

    assert result["status"] == "success"
    assert result["reply"] == "直接回答"


# ================================================================
# 六、流式展示（AC-12 / FR-28 / FR-31）
# ================================================================

def _fake_stream(monkeypatch, pieces):
    """把 chat_stream 替换成伪流：逐 token 回调，返回完整结果。"""
    def fake_chat_stream(messages, tools=None, on_token=None, **kwargs):
        for piece in pieces:
            on_token(piece)
        return {"content": "".join(pieces), "usage": {}}

    monkeypatch.setattr(llm_adapter, "chat_stream", fake_chat_stream)


def test_streamed_candidate_is_buffered_while_gate_is_open(tmp_path, monkeypatch):
    """AC-12 / FR-28：门开着时 token 照收但静默缓冲，不建持久面板，
    last_streamed 保持 False（不误抑制最终提交）。"""
    pieces = ["任务完成，", "一切正常。"]
    _fake_stream(monkeypatch, pieces)

    gate = make_gate(tmp_path)
    console = Console(file=io.StringIO(), width=100)
    adapter = StreamingAdapter([], console, THEME, publish_gate=gate)

    # 有未验证的修改 → 静默缓冲
    write_and_track(gate, tmp_path, "a.py", "x\n")
    response = adapter.call([{"role": "user", "content": "hi"}])

    assert response.content == "".join(pieces)  # token 确实收到了
    assert adapter.last_streamed is False       # 但没有持久展示

    # 验证通过后 → 恢复正常流式展示
    validate(gate)
    adapter.call([{"role": "user", "content": "hi"}])
    assert adapter.last_streamed is True


def test_streamed_clean_task_publishes_normally(tmp_path, monkeypatch):
    """FR-27：干净任务（无 Gate / 无未验证修改）保持原流式体验。"""
    _fake_stream(monkeypatch, ["你好"])

    console = Console(file=io.StringIO(), width=100)
    # 没配 publish_gate → 永远展示（NFR-1）
    adapter = StreamingAdapter([], console, THEME)

    adapter.call([{"role": "user", "content": "hi"}])
    assert adapter.last_streamed is True


# ================================================================
# 六、审查补充的边界回归
# ================================================================

def test_failed_write_that_partially_changes_file_requires_validation(tmp_path):
    """写工具报错但已部分落盘时，不能被默认版本放行。"""
    gate = make_gate(tmp_path)
    gate.before_tool("write_file", {"path": "src/a.py"})
    target = tmp_path / "src/a.py"
    target.parent.mkdir(parents=True)
    target.write_text("部分内容\n", encoding="utf-8")
    gate.after_tool("write_file", True, {"path": "src/a.py"})

    assert gate.evaluate("完成").action == "continue"


def test_unrelated_command_containing_tests_token_does_not_verify(tmp_path):
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate, command="git status tests", tool_name="run_bash")

    assert gate.evaluate("完成").action == "continue"


def test_uv_run_with_options_is_recognized_as_validation(tmp_path):
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    validate(gate, command="uv run --locked pytest -q", tool_name="run_bash")

    assert gate.evaluate("完成").action == "accept"


def test_iteration_limit_does_not_return_stale_candidate_after_new_change(tmp_path):
    gate = make_gate(tmp_path)
    write_and_track(gate, tmp_path, "src/a.py", "v1\n")
    gate.evaluate("v1 候选回答")
    write_and_track(gate, tmp_path, "src/a.py", "v2\n")

    assert gate.take_pending_final() is None


def test_read_file_end_line_only_starts_from_first_line(tmp_path):
    target = tmp_path / "big.py"
    target.write_text("l1\nl2\nl3\n", encoding="utf-8")

    result = _read(tmp_path, {"path": "big.py", "end_line": 2})

    assert result.error is False
    assert "1| l1" in result.content
    assert "2| l2" in result.content


def test_read_file_character_limit_reports_actual_last_line(tmp_path):
    target = tmp_path / "big.py"
    target.write_text(
        "\n".join("line %d" % i for i in range(1, 30)), encoding="utf-8"
    )
    tools = CodingTools(tmp_path, max_output_chars=45)
    result = tools.execute(ToolCall(
        id="r1",
        name="read_file",
        arguments={"path": "big.py", "start_line": 1, "end_line": 29},
    ))

    assert result.error is False
    assert result.metadata["truncated"] is True
    next_line = result.metadata["end_line"] + 1
    assert "start_line=%d" % next_line in result.content
