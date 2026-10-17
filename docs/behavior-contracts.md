# v2 行为契约

[返回 v2 架构](architecture.md) · [开发计划](plan.md) · [术语表](../CONTEXT.md)

本文列出 v2 必须保持的可观察行为。每条规则有一个编号，例如 `C3.2` 表示第 3 组第 2 条，[开发计划](plan.md)按编号说明每个阶段要通过哪些规则。

第 1–10 组从 v1 的测试中提炼，每条后面是 v1 中覆盖它的测试（标签 `v1-final`）；v2 的测试按同样的规则改写，改写后更新引用。第 11–18 组是 v2 新增模块的规则，没有对应的 v1 测试。标注「v2 调整」的规则与 v1 行为不同，标注「v2 补充」的规则是 v1 已有、但之前没有单独列出的行为。

## 1. 任务与循环

- **C1.1** 模型返回文本、不带工具调用并且完成判定通过时，结果为 `success`，`reply` 等于这段文本；没有记忆写回时，结果只有 `status` 和 `reply` 两个字段。`tests/test_agent_loop.py::TestMachineLoop::test_loop_with_one_tool_call`
- **C1.2** 模型只返回文本、没有表示完成时，结果为 `need_input`，这句回复同样写入会话记录。`tests/test_session_store.py::TestMachineLoopWithSessionStore::test_need_input_reply_also_written`
- **C1.3** 任务开始前已经取消时，结果严格等于 `{"status": "cancelled"}`，不调用模型；用户消息已经写入会话。`tests/test_runtime_assembly.py::test_public_entry_cancels_without_calling_model`
- **C1.4** 模型每轮都返回工具调用并且超过 `max_turns` 时，结果为 `failed`，`error` 为 `max_turns`。`tests/test_agent_loop.py::TestMachineLoop::test_loop_max_turns`
- **C1.5** 权限暂停和批准不重置轮数预算：恢复后接着原来的轮次计数，事件里的 `turn` 与实际计数一致，从 0 开始。`tests/test_task_lifecycle.py::test_permission_pauses_do_not_reset_turn_budget`
- **C1.6** 最后一个工具执行完后发生取消时，即使轮数同时耗尽，结果也是 `cancelled`，最后一个事件是 `cancelled`，不出现 `failed`。`tests/test_release_boundaries.py::test_cancellation_after_last_tool_takes_precedence_over_turn_limit`
- **C1.7** 未知工具被拒绝后循环继续运行，模型可以正常完成。`tests/test_agent_loop.py::TestMachineLoop::test_loop_with_permission_deny`
- **C1.8** 连续 3 次使用同一工具和同一参数时停止，结果为 `failed`，`error` 为 `guard_stopped`；只有 2 次、工具不同、同一工具但参数不同、历史为空时都不停止。`tests/test_agent_loop.py::TestGuardManager::test_repeat_same_tool_same_args`、`tests/test_agent_loop.py::TestGuardManager::test_same_tool_different_args`
- **C1.9** 遇到上下文超限时，强制压缩一次并且只重试一次，同时发出 `compaction_fallback` 事件，`kind` 为 `context_overflow_retry`。`tests/test_memory_compaction_resilience.py::TestLoopContextOverflowRecovery::test_compacts_once_and_retries`
- **C1.10** 强制压缩没有减少内容时直接失败，错误信息包含「无法自动恢复」，不再请求模型。`tests/test_memory_compaction_resilience.py::TestLoopContextOverflowRecovery::test_fails_when_compaction_makes_no_progress`
- **C1.11** 重试后仍然超限时失败，错误信息提示 `/compact`，模型最多请求 2 次；没有配置压缩时直接失败。`tests/test_memory_compaction_resilience.py::TestLoopContextOverflowRecovery::test_fails_when_second_call_still_overflows`、`tests/test_memory_compaction_resilience.py::TestLoopContextOverflowRecovery::test_fails_without_context_manager`
- **C1.12** v2 补充：模型既没有返回文本，也没有返回工具调用时，结果为 `failed`，`error` 为 `no_tool_call`，不写入空回复。

## 2. 工具批次与权限

- **C2.1** 同一回复中的多个调用按原顺序处理：遇到需要审批的调用时暂停，暂停前已经执行的调用不重复执行，批次完成前不请求模型；恢复后剩余调用按顺序执行，下一次请求模型前每个调用都有对应回执。`tests/test_task_lifecycle.py::test_batch_resumes_remaining_calls_in_order_without_reexecuting`
- **C2.2** 拒绝只影响当前调用：被拒绝的调用不执行，回执写「用户拒绝了这次操作」，同批其余调用按各自的权限继续处理。`tests/test_task_lifecycle.py::test_denied_tool_keeps_explicit_rule_for_rest_of_batch`、`tests/test_agent_run.py::test_agent_run_reports_denied_tool_to_model_without_executing_it`
- **C2.3** 等待审批期间取消后，随后到达的批准不执行工具；最后三个事件依次为 `permission_resolved`、`post_tool`（未执行）、`cancelled`。`tests/test_task_lifecycle.py::test_approval_arriving_after_cancel_does_not_execute_tool`
- **C2.4** 取消后，批次中所有剩余调用都得到「未执行」回执，最后一条会话记录是最后那个调用的回执。`tests/test_task_lifecycle.py::test_cancel_after_pause_accounts_for_rest_of_batch`、`tests/test_release_boundaries.py::test_cancellation_after_remaining_batch_tool_is_reported`
- **C2.5** 权限决定只能提交一次：重复调用 `resolve_permission` 抛出 `RuntimeError`，工具不会重复执行。`tests/test_task_lifecycle.py::test_consumed_permission_decision_cannot_run_tool_twice`
- **C2.6** 恢复执行时，剩余批次全部完成之后才做上下文压缩。`tests/test_release_boundaries.py::test_remaining_batch_executes_before_context_compaction`
- **C2.7** Coding 默认权限：读类工具自动放行；`write_file`、`edit_file`、`run_test`、`run_bash` 需要询问；未知工具拒绝。`tests/test_coding_write_tools.py::TestWritePermissions::test_unknown_tool_deny`、`tests/test_plan_mode.py::TestPermissionManagerPlanMode::test_normal_mode_unchanged`
- **C2.8** 工具集合就是能力边界：未注册的工具一律拒绝；工具没有声明权限时默认自动放行。`tests/test_tool_manager.py::TestPermissionManagerWithToolManager::test_unregistered_tool_is_denied`、`tests/test_tool_manager.py::TestToolManager::test_get_permission_unregistered`
- **C2.9** 执行未注册的工具时返回 `invalid_args` 错误回执，并带上原来的 `tool_call_id`。`tests/test_tool_manager.py::TestToolManager::test_unknown_tool`
- **C2.10** 计划模式下写工具和命令工具全部拒绝，只读工具仍然自动放行；无人值守模式不能绕过计划模式。`tests/test_plan_mode.py::TestPermissionManagerPlanMode::test_write_tools_denied_in_plan_mode`、`tests/test_plan_mode.py::TestPermissionManagerPlanMode::test_auto_approve_does_not_bypass_plan_mode`
- **C2.11** 无人值守模式把询问变成自动放行，不改变拒绝。`tests/test_memory_compaction_resilience.py::TestMemoryWriteSafety::test_auto_approve_turns_ask_into_auto`
- **C2.12** 保护路径：写工具写入 `.env`、`.git/`、`.autocoding/MEMORY.md` 时拒绝；读工具不受这条限制。`tests/test_protected_paths.py::test_protected_path_check_blocks_sensitive_write_paths`、`tests/test_protected_paths.py::test_protected_path_check_only_targets_write_tools`
- **C2.13** 按参数决定权限：`memory` 工具的 `add` 自动放行，`replace` 和 `remove` 需要询问。`tests/test_memory_compaction_resilience.py::TestMemoryWriteSafety::test_add_is_auto_replace_and_remove_ask`、`tests/test_memory_tool.py::test_permission_manager_wiring_allows_memory`
- **C2.14** v2 调整：权限规则按优先级匹配，第一条给出拒绝或询问的规则生效；规则执行时抛出异常按拒绝处理。v1 由按名称注册的阻断检查实现。`tests/test_hook_manager.py::test_check_returns_first_deny_or_ask`、`tests/test_hook_manager.py::test_check_callback_error_fails_closed`
- **C2.15** 批准之后仍然做工作区路径检查：越界路径的写入返回工具错误，不创建文件，任务继续进行。`tests/test_harness_events.py::test_permission_resolution_event_precedes_tool_outcome`

## 3. 会话记录

- **C3.1** 每条记录占一行，中文按明文保存，读回的内容和顺序与写入一致；没有追加记录时不创建文件。`tests/test_session_store.py::TestSessionStore::test_append_then_load_roundtrip`、`tests/test_session_store.py::TestSessionStore::test_lazy_creation`
- **C3.2** 读取时跳过损坏的半行；之后追加的新记录仍然单独占一行并且可以读出。`tests/test_session_store.py::TestSessionStore::test_load_skips_broken_line`、`tests/test_session_store.py::TestSessionStore::test_append_after_broken_line_keeps_new_message_readable`
- **C3.3** 会话记录只追加，存储接口没有覆盖历史的方法。`tests/test_memory_compaction_resilience.py::test_session_store_has_no_overwrite_method`
- **C3.4** 用户消息由会话写入；循环只写它自己产生的助手消息（含工具调用）、工具回执和最终回复。`tests/test_session_store.py::TestMachineLoopWithSessionStore::test_tool_turn_messages_written_to_jsonl`、`tests/test_agent_session.py::test_session_starts_run_from_persisted_history`
- **C3.5** 工具回执写入失败时，结果为 `failed`，`error` 为 `session_write_failed`，并附上 `tool_result`；之后不再请求模型、不再执行工具，也不能再次提交权限决定。自动执行的工具同样适用。`tests/test_agent_run.py::test_agent_run_stops_with_tool_result_when_session_write_fails`、`tests/test_session_store.py::TestMachineLoopWithSessionStore::test_auto_tool_stops_when_its_result_cannot_be_saved`
- **C3.6** 最终回复写入失败时，结果为 `failed`，`error` 为 `session_write_failed`，并附上 `reply`。`tests/test_session_store.py::TestMachineLoopWithSessionStore::test_final_reply_is_not_reported_saved_when_append_fails`
- **C3.7** 回执写入失败和取消同时发生时以 `failed` 为准，事件中不出现 `cancelled`。`tests/test_release_boundaries.py::test_receipt_write_failure_is_not_hidden_by_cancellation`
- **C3.8** 修复中断的工具调用：只给缺少回执的调用补一条工具回执，内容包含「执行状态未知」和「不要直接重试」；修复可以重复执行，结果不变；调用与回执配对完整时不做改动。`tests/test_cli_polish.py::TestRepairDanglingToolResults::test_repairs_missing_tool_result`、`tests/test_cli_polish.py::TestRepairDanglingToolResults::test_repairs_only_missing_one`
- **C3.9** 恢复会话时自动执行上述修复并刷新视图。`tests/test_tui_profiles.py::test_resume_repairs_tool_with_unknown_execution_state`、`tests/test_agent_session.py::test_session_repairs_interrupted_tool_call_and_refreshes_view`
- **C3.10** `/clear` 只清空进程内视图，不删除会话记录；之后恢复原会话可以看到全部历史。`tests/test_agent_session.py::test_session_clear_hides_old_history_without_deleting_jsonl`、`tests/test_textual_cli.py::test_textual_resume_restores_real_jsonl_without_deleting_clear_history`
- **C3.11** v2 调整：手动压缩后，视图变为「摘要加之后的新消息」；会话记录追加一条 `compaction` 记录，已有记录逐字节不变。v1 只改变进程内视图。`tests/test_agent_session.py::test_session_compaction_keeps_summary_and_later_jsonl_messages`、`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_compaction_never_rewrites_session_jsonl`
- **C3.12** 恢复参数：不传表示新会话；空字符串表示最近修改的会话，没有会话时报错；指定 id 时恢复该会话；id 不存在时报错，不静默新建。`tests/test_session_store.py::TestOpenSession::test_resume_latest_picks_newest`、`tests/test_session_store.py::TestOpenSession::test_resume_missing_id_errors`
- **C3.13** 会话 id 不能跳出当前 Profile 的会话目录（`../`、`..\`、`C:`、`..` 都报错）。`tests/test_profiles.py::test_resume_cannot_escape_profile`
- **C3.14** 会话列表的标题取第一条用户消息的前 50 个字，按修改时间倒序排列；目录不存在时返回空列表；新会话 id 不重复。`tests/test_session_store.py::TestSessionStore::test_list_sessions_title_and_order`、`tests/test_session_store.py::TestSessionStore::test_new_session_id_unique`
- **C3.15** 切换会话时，视图、执行循环、历史召回排除的当前会话和诊断记录文件一起切换。`tests/test_agent_session.py::test_session_switch_resets_view_and_updates_runtime_store`、`tests/test_profiles.py::test_companion_uses_shared_loop_and_separate_trace`
- **C3.16** 通过公共入口用会话 id 重新打开会话时，可以拿到完整的用户和助手历史。`tests/test_runtime_assembly.py::test_public_entry_opens_and_resumes_session_without_component_assembly`
- **C3.17** 宿主可以提供只实现 `session_id`、`load()`、`append()` 的自定义存储；这时不从本地旧会话召回内容。`tests/test_runtime_assembly.py::test_public_entry_accepts_store_without_file_path_when_trace_enabled`

## 4. 上下文与压缩

- **C4.1** 没有超过限制时原样返回；超过限制时保留系统提示词和最近的消息。`tests/test_context_manager.py::TestContextManager::test_no_compact_if_under_limit`、`tests/test_context_manager.py::TestContextManager::test_compact_keeps_system_and_recent`
- **C4.2** 最新的工具批次（工具调用和全部回执）即使超过限制也整体保留；保留部分不能以孤立的工具回执开头。`tests/test_context_manager.py::TestContextManager::test_compact_preserves_latest_tool_batch_when_it_exceeds_limit`、`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_excerpt_keeps_tool_pairing_intact`
- **C4.3** 被移出视图的旧消息不少于 4 条时才请求摘要；结果是「系统提示词、一条 `[历史摘要]`、近期消息」。少于 4 条时只截断。`tests/test_context_summary.py::TestContextSummary::test_02_summary_triggered_when_old_messages_ge_4`、`tests/test_context_summary.py::TestContextSummary::test_03_no_summary_when_old_messages_lt_4`
- **C4.4** 摘要失败（异常、空字符串、超时、HTTP 错误）时改用本地确定性摘录：摘录以「仅作历史参考」开头，记录摘录模式和错误原因；去掉摘录后，结果与纯截断完全一致。`tests/test_context_summary.py::TestContextSummary::test_04_fallback_excerpt_on_exception`、`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_transport_failures_use_excerpt`
- **C4.5** 摘录包含原始目标、报错现场和涉及的文件，总长度不超过上限。`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_excerpt_contains_goal_errors_and_files`、`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_excerpt_respects_char_cap`
- **C4.6** 摘要失败后进入冷却期，同一批旧消息不再重复请求模型；手动压缩可以绕过冷却。`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_failure_is_cooled_down_not_retried`、`tests/test_memory_compaction_resilience.py::TestSummaryFallback::test_force_bypasses_cooldown`
- **C4.7** 摘要关闭时只截断，Token 预算照样生效；摘要请求使用 Profile 指定的模型，不修改全局默认模型。`tests/test_session_store.py::TestContextManagerWiring::test_summary_disabled_falls_back_to_truncate`、`tests/test_profiles.py::test_custom_model_does_not_change_global_adapter`
- **C4.8** Token 计数：已知模型使用官方编码，并且不被之后的初始化覆盖；特殊 token 文本按普通文本计数；请求估算包含工具定义和消息元数据。`tests/test_token_accounting.py::test_known_model_uses_official_encoding_without_init_overwrite`、`tests/test_token_accounting.py::test_request_estimate_includes_tools_and_message_metadata`
- **C4.9** 压缩固定使用自己的模型计数，并把工具定义的开销算进是否需要压缩的判断。`tests/test_token_accounting.py::test_context_manager_keeps_its_model_and_counts_tool_overhead`
- **C4.10** 窗口来源优先级：手动配置最高并且不查询模型服务，其次是模型服务报告，最后是默认值；手动配置不是正整数时报错；Profile 的 `context_budget` 可以直接给出预算。`tests/test_session_store.py::TestContextBudgetResolution::test_explicit_context_length_has_highest_priority`、`tests/test_token_accounting.py::test_window_source_and_profile_override`
- **C4.11** 预算计算：大窗口按比例取（100000 得到 80000）；小窗口预留输出空间（窗口 8192、输出 4096 得到 3072）；窗口不大于预留空间时报错；配置变化后重新计算，不复用旧值。`tests/test_session_store.py::TestContextBudgetResolution::test_budget_reserves_output_for_small_window`、`tests/test_token_accounting.py::test_window_refresh_does_not_reuse_old_model_settings`
- **C4.12** 查询模型窗口：布尔值、浮点数、非正数、空值都视为无效；字符串形式的整数可以接受；只使用匹配目标模型的元数据；llama.cpp 使用 `/props` 中实际部署的 `n_ctx`，不用 `n_ctx_train`，`/props` 失败时返回未知。`tests/test_token_accounting.py::test_provider_rejects_invalid_window`、`tests/test_token_accounting.py::test_llama_uses_deployed_context_not_training_window`
- **C4.13** 宿主显式给出预算时不查询模型窗口；不使用技能时不扫描技能目录，可以离线启动。`tests/test_order_assistant_example.py::test_order_assistant_runs_business_tools_without_coding_dependencies`
- **C4.14** 跨会话自动召回：在系统提示词之后插入一条临时消息，不修改会话视图，也不写入会话记录。`tests/test_context_selector.py::test_selector_injects_relevant_history_without_mutating_messages`、`tests/test_context_selector.py::test_machine_loop_uses_selected_view_without_persisting_injection`
- **C4.15** 召回排除当前会话；无关历史不注入；命中条数和字符数都有上限；同一条用户消息的召回结果复用；召回出错时原样返回；单个有区分度的词也可以召回。`tests/test_context_selector.py::test_selector_excludes_current_session_and_skips_unrelated_history`、`tests/test_context_selector.py::test_selector_failure_does_not_block_agent_request`
- **C4.16** 请求组装顺序：系统提示词、按固定顺序的注入内容（项目指令、技能清单、记忆快照）、会话历史；只对本次请求生效的临时注入不保留。`tests/test_runtime.py::test_runtime_build_messages_keeps_history_at_end`、`tests/test_runtime.py::test_runtime_extra_injection_is_not_persisted_in_registry`
- **C4.17** 每次请求末尾附加一条临时 `<agent_status>` 用户消息（目标、轮次、工具调用数、最近工具、最近失败），不写入会话视图和会话记录。`tests/test_agent_status.py::test_status_is_ephemeral_and_updates_after_tool_call`、`tests/test_agent_status.py::test_status_records_denied_permission_resolved_by_agent_run`

## 5. 事件与宿主交互

- **C5.1** 事件观察方的作用范围是整个会话，同一会话的多次任务都会通知它；观察方的返回值不能控制执行。`tests/test_harness_events.py::test_public_event_observer_is_scoped_to_session_and_cannot_control_run`
- **C5.2** 暂停时结果为 `permission_required`，只暴露脱敏后的 `permission_request`（`tool_name`、`tool_call_id`、`turn`、`summary`、`details`），不暴露批次对象或消息列表。`tests/test_harness_events.py::test_permission_pause_exposes_only_redacted_request_view`、`tests/test_order_assistant_example.py::test_order_assistant_pause_exposes_redacted_request`
- **C5.3** 脱敏规则：参数名包含 token 或 secret 时，值替换为 `[REDACTED]`；值中出现凭据格式时替换；超过 200 字符时替换为 `[HIDDEN: value exceeds 200 characters]`。事件和结果的字符串表示中都不能出现原始密钥。`tests/test_harness_events.py::test_permission_pause_exposes_only_redacted_request_view`
- **C5.4** `permission_required` 事件的数据严格等于 `{"request": <与运行结果相同的请求视图>}`；`permission_resolved` 包含 `decision`（`approved` 或 `denied`）、`tool_call_id`、`turn`，并且一定早于这个工具的 `post_tool`。`tests/test_harness_events.py::test_permission_resolution_event_precedes_tool_outcome`
- **C5.5** v2 调整：只有一种事件订阅方式。外部观察方收到的 `post_tool` 包含 `error`，不包含 `result_content`；核心内部订阅方收到完整数据。`tests/test_harness_events.py::test_permission_resolution_event_precedes_tool_outcome`、`tests/test_agent_run.py::test_agent_run_approves_tool_and_resumes_with_same_task_state`
- **C5.6** 宿主修改事件数据或请求视图副本，不影响实际执行的参数和轮次。`tests/test_harness_events.py::test_mutating_public_event_does_not_change_tool_execution`
- **C5.7** 观察方抛出的异常被隔离，任务照常完成；日志只记录异常类型，不记录异常消息；一个观察方失败不影响其他观察方收到同一事件。`tests/test_harness_events.py::test_observer_exception_isolated_and_not_written_to_logs`、`tests/test_hook_manager.py::test_event_observers_share_event_and_one_failure_does_not_stop_others`
- **C5.8** 每个任务只有一个结束事件，并且是最后一个事件：`done`、`cancelled`、`failed` 三者之一。`tests/test_order_assistant_example.py::test_order_assistant_runs_business_tools_without_coding_dependencies`、`tests/test_release_boundaries.py::test_receipt_write_failure_is_not_hidden_by_cancellation`
- **C5.9** 运行结果的状态只有 `success`、`need_input`、`permission_required`、`cancelled`、`failed`。`tests/test_runtime_assembly.py::test_public_entry_resolves_permission_with_one_run`、`tests/test_completion_gate.py::test_synthetic_verification_nudge_is_not_persisted`
- **C5.10** 同一个任务对象可以暂停后继续，保持同一个取消令牌和任务状态；`cancel()` 只标记这个任务自己的令牌。`tests/test_agent_run.py::test_agent_run_approves_tool_and_resumes_with_same_task_state`、`tests/test_agent_run.py::test_agent_run_cancel_marks_its_own_token`
- **C5.11** 诊断记录默认关闭，不创建目录；开启后写入 `runs/<session_id>.jsonl`，包含配置、模型请求、模型回复和结束记录，第一条记录带 Profile 名称，并与会话记录分开存放。`tests/test_profiles.py::test_trace_is_disabled_by_default`、`tests/test_profiles.py::test_companion_uses_shared_loop_and_separate_trace`

## 6. 记忆

- **C6.1** 本地记忆新增：自动创建目录，每条一行并以 `- ` 开头，多行内容合并为一行；内容完全重复时返回成功并提示「已存在」，不写入；目标不合法时报错。`tests/test_memory_manager.py::test_add_multiline_content_flattened`、`tests/test_memory_manager.py::test_add_duplicate_rejected`
- **C6.2** 超过容量时返回失败，消息包含「已满」、当前全部条目和替换或删除的提示，文件不变；替换后超过容量的处理相同。`tests/test_memory_manager.py::test_add_over_limit_returns_error_with_entries`、`tests/test_memory_manager.py::test_replace_over_limit`
- **C6.3** 替换和删除按子串唯一匹配：没有匹配时提示「没有找到」并附上条目，多处匹配时要求「更精确」；通过工具调用时，匹配失败属于 `execution` 错误。`tests/test_memory_manager.py::test_replace_multiple_match`、`tests/test_memory_tool.py::test_match_failure_is_execution_error`
- **C6.4** 注入内容：两个记忆文件都为空时不注入；有内容时带说明和容量标题，只包含非空的部分；手工改得超长的文件按上限截断，并标注「已截断」。`tests/test_memory_manager.py::test_render_injection_headers`、`tests/test_memory_manager.py::test_render_injection_overlong_file_clipped`
- **C6.5** 写入安全：并发新增不丢条目；原子替换失败时旧文件保持不变，临时文件被清理；读取旧文件失败时中止写入，不把旧内容当成空文件覆盖。`tests/test_memory_compaction_resilience.py::TestMemoryWriteSafety::test_concurrent_adds_do_not_lose_entries`、`tests/test_memory_compaction_resilience.py::TestMemoryWriteSafety::test_read_failure_does_not_overwrite_old_memory`
- **C6.6** 记忆工具：写入异常转成工具错误，不中断循环；`action` 和 `target` 必填；新增需要 `content`；替换需要 `old_text`；未知的 `action` 或 `target` 返回 `invalid_args`。`tests/test_memory_compaction_resilience.py::TestMemoryWriteSafety::test_write_error_becomes_tool_error_not_crash`、`tests/test_memory_tool.py::test_replace_requires_old_text`
- **C6.7** `target=memory` 写入工作区的 `.autocoding/MEMORY.md`，`target=user` 写入用户目录的 `USER.md`；不同 Profile 不共享用户记忆，也不共享召回历史。`tests/test_memory_tool.py::test_add_memory_writes_project_file`、`tests/test_profiles.py::test_profiles_do_not_share_memory_or_history`
- **C6.8** 外部记忆作用域不允许空标识；记忆服务必须严格按用户、团队、Agent 匹配作用域，并按 `max_items` 截断。`tests/test_external_memory.py::test_scope_rejects_blank_identifiers`、`tests/test_external_memory.py::test_in_memory_provider_isolates_exact_scope_and_caps_hits`
- **C6.9** 召回时不信任记忆服务的返回：重新过滤不属于当前作用域的结果，执行条数和 Token 预算限制（超出预算的条目跳过），请求已经没有空间时不请求记忆服务；不修改会话视图。`tests/test_external_memory.py::test_recall_selector_enforces_scope_items_and_token_budget_without_mutating_messages`、`tests/test_external_memory.py::test_recall_selector_skips_provider_when_request_has_no_room`
- **C6.10** 外部召回在每个新任务中重新查询，不使用过期缓存；记忆服务抛出异常或返回非法结果时记录「外部记忆召回失败」并跳过，不中断任务。`tests/test_external_memory.py::test_recall_selector_refreshes_same_question_in_new_run`、`tests/test_external_memory.py::test_recall_failure_is_visible_and_skipped`
- **C6.11** 外部召回内容只临时加入当前请求，不写入会话记录；传入记忆服务但没有作用域时抛出 `ValueError`。`tests/test_external_memory.py::test_public_entry_uses_temporary_external_memory_without_persisting_it`、`tests/test_external_memory.py::test_public_entry_requires_scope_with_provider`
- **C6.12** 远端记忆服务（腾讯）：必须有 API key，远端地址必须使用 HTTPS（本机地址可以用 HTTP）；作用域缺少团队或 Agent 时在发出网络请求前拒绝；不跟随重定向；响应中出现其他作用域或格式非法时拒绝。`tests/test_tencent_memory.py::test_tencent_rejects_missing_key_and_cleartext_remote_endpoint`、`tests/test_tencent_memory.py::test_tencent_rejects_foreign_or_invalid_response`
- **C6.13** 远端召回超时时跳过召回，日志中不出现 key，会话记录只有用户消息和助手回复两条。`tests/test_tencent_memory.py::test_tencent_timeout_skips_recall_without_leaking_key_or_writing_jsonl`
- **C6.14** 写回只在显式提供写入器时启用，并且只在任务成功后执行一次；写入内容只有作用域、会话 id、原始用户消息和最终助手回复，结果附带 `memory_writeback={"status": "saved"}`。`tests/test_memory_writeback.py::test_explicit_writer_captures_only_successful_final_turn`、`tests/test_memory_writeback.py::test_writeback_is_off_without_explicit_writer`
- **C6.15** `need_input`、`cancelled` 和权限暂停时不写回；权限恢复后成功的任务只写回原始用户消息和最终回复。`tests/test_memory_writeback.py::test_permission_resumed_run_captures_only_user_and_final_reply`、`tests/test_memory_writeback.py::test_writer_skips_cancelled_turn`
- **C6.16** 写回超时（结果不确定）时返回 `{"status": "unknown", "error_type": ...}`，只请求一次，不重试；明确失败时返回 `failed`；这两种情况下任务本身仍然是 `success`。`tests/test_memory_writeback.py::test_write_timeout_is_visible_and_not_retried`、`tests/test_tencent_memory.py::test_tencent_capture_timeout_is_uncertain_and_business_error_is_definite`

## 7. 完成验证

- **C7.1** 只看文件的净变化：临时文件建了又删、文件改后又恢复原样，都不算修改；越界路径不跟踪，也不会导致异常。`tests/test_completion_gate.py::test_temporary_file_created_then_deleted_is_not_a_net_change`、`tests/test_completion_gate.py::test_file_restored_to_baseline_is_not_a_net_change`
- **C7.2** 写工具报错但文件已经部分写入磁盘时，仍然算作修改，需要验证。`tests/test_completion_gate.py::test_failed_write_that_partially_changes_file_requires_validation`
- **C7.3** 代码有净变化但没有验证时：第一次要求继续，提示里带上修改的路径；第二次仍然没有验证就失败，交付原来的候选回答并加上「未验证」标记。`tests/test_completion_gate.py::test_real_code_change_requires_fresh_verification`
- **C7.4** 验证证据必须是新的：最后一次真实修改之前跑的测试已经过期；验证之后再改代码，旧证据失效；之后只创建又删除临时文件，不影响已有证据。`tests/test_completion_gate.py::test_change_after_successful_test_invalidates_old_evidence`、`tests/test_completion_gate.py::test_temporary_write_after_validation_does_not_invalidate_evidence`
- **C7.5** 有效验证必须同时满足：工具没有报错，退出码为 0，命令在词级别命中测试或检查命令（例如 `uv run --locked pytest`）；`git status`、`git checkout`、`git status tests` 不算验证。`tests/test_completion_gate.py::test_git_checkout_does_not_match_check_token`、`tests/test_completion_gate.py::test_error_result_never_counts_as_validation`
- **C7.6** 只修改了 `.md` 文档时不要求代码验证，最终回复的标注如实说明是文档修改。`tests/test_completion_gate.py::test_doc_only_change_skips_code_verification`
- **C7.7** 新任务开始时清空上一个任务的全部证据和候选回答。`tests/test_completion_gate.py::test_start_task_clears_previous_evidence`
- **C7.8** 要求继续后如果只补做了验证，最终回复复用原来的候选回答并加上「已验证」标注，模型的验证回执不能替换实质回答；验证期间又发生新的真实修改时，旧候选失效，以新回答为准。`tests/test_completion_gate.py::test_pending_candidate_is_reused_after_verification_only_continuation`、`tests/test_completion_gate.py::test_pending_candidate_expires_after_new_real_change`
- **C7.9** 验证提示（`[完成验证]`）只发给模型，不写入会话记录；被要求继续的候选回答也不写入，最终交付的回答在会话记录中只出现一次。`tests/test_completion_gate.py::test_synthetic_verification_nudge_is_not_persisted`、`tests/test_completion_gate.py::test_committed_final_response_is_displayed_once`
- **C7.10** 最终仍然没有验证时，结果为 `failed`，`error` 为 `verification_required`，`reply` 是带「未验证」标记的候选回答。`tests/test_completion_gate.py::test_denied_verification_delivers_candidate_once`
- **C7.11** 轮数耗尽时，如果有等待交付的候选回答，就带「未验证」标记一起交付（`error` 仍为 `max_turns`）；之后发生新修改时，不返回已经过期的候选。`tests/test_completion_gate.py::test_iteration_limit_preserves_pending_candidate`、`tests/test_completion_gate.py::test_iteration_limit_does_not_return_stale_candidate_after_new_change`
- **C7.12** 权限暂停恢复后，候选回答和验证证据都保留。`tests/test_completion_gate.py::test_agent_run_resume_preserves_pending_candidate_and_evidence`
- **C7.13** 没有启用完成检查时，模型表示完成就算成功；Coding 的完成验证默认关闭，可以在 YAML 中开启，环境变量优先于 Profile，也可以显式关闭。`tests/test_completion_gate.py::test_machine_loop_without_gate_keeps_old_behavior`、`tests/test_profiles.py::test_environment_overrides_profile_verification_setting`

## 8. Profile 与工具集合

- **C8.1** 不合法的 Profile 配置在任务开始前抛出 `ValueError`：名称包含路径穿越、与内置名称冲突、未知类型、review 配置了 `run_bash`、字段类型错误（布尔值和列表都严格检查）、未知字段。`tests/test_profiles.py::test_invalid_config_fails_before_runtime`
- **C8.2** review：即使开启无人值守模式，`write_file`、`edit_file`、`run_bash`、`run_test`、`memory` 也一律拒绝，执行时返回错误；不启用完成验证；模型看到的工具定义里没有写工具。`tests/test_profiles.py::test_review_denies_writes_even_with_auto_approve`、`tests/test_harness_review_example.py::test_read_only_review_example_uses_harness_and_persists_session`
- **C8.3** companion 不加载仓库的项目指令文件，不修改代码也能完成任务；没有技能时，工具只有 `memory` 和 `recall_history`。`tests/test_profiles.py::test_companion_uses_shared_loop_and_separate_trace`、`tests/test_profiles.py::test_empty_skills_hide_and_disable_skill_tools`
- **C8.4** 技能清单为空时，隐藏 `search_skills` 和 `load_skill`，直接调用也返回错误；空清单时的搜索结果说明「当前 Profile 没有可用技能」，不建议模型换关键词重试。`tests/test_profiles.py::test_empty_skills_hide_and_disable_skill_tools`、`tests/test_profiles.py::test_empty_skill_search_explains_profile_scope`
- **C8.5** 技能清单、搜索和加载共用同一个允许列表，不在列表里的技能不能搜到，也不能加载。`tests/test_profiles.py::test_skills_menu_search_and_load_share_allowlist`
- **C8.6** 每个 Profile 的状态目录是 `.autocoding/profiles/<name>`（会话、诊断记录、输入历史都在里面）；切换 Profile 时隔离历史和能力，旧会话记录保留。`tests/test_profiles.py::test_custom_profile_config_and_legacy_paths`、`tests/test_textual_cli.py::test_textual_profile_switch_isolates_history_and_capabilities`
- **C8.7** 自定义 YAML 可以设置 `skills`、`model`、`context_budget`、`prompt`；自定义模型不改变全局默认模型。`tests/test_profiles.py::test_custom_profile_config_and_legacy_paths`、`tests/test_profiles.py::test_custom_model_does_not_change_global_adapter`
- **C8.8** 宿主可以注册自定义业务工具（带权限声明）；这时工具集合和发给模型的工具定义只包含这些业务工具，两者完全一致。`tests/test_order_assistant_example.py::test_live_order_assistant_supplies_business_schemas_to_model`、`tests/test_runtime_assembly.py::test_public_entry_composes_tools_memory_and_session`
- **C8.9** coding 的工具集合按以下顺序排列：`read_file`、`list_dir`、`glob`、`grep`、`write_file`、`edit_file`、`run_test`、`run_bash`、`load_skill`、`search_skills`、`memory`、`recall_history`。`tests/test_coding_tools.py::TestCodingTools::test_get_schemas`
- **C8.10** 模型配置（`.env`）从调用方的当前工作目录读取。`tests/test_caller_environment.py::test_settings_loads_model_config_from_callers_working_directory`

## 9. Coding 工具

- **C9.1** 工作区路径限制：路径穿越、空路径、纯空白路径都视为无效；越出工作区的读写返回 `permission` 错误。`tests/test_coding_sandbox.py::TestWorkspaceSandbox::test_resolve_path_traversal`、`tests/test_coding_write_tools.py::TestWriteFile::test_write_path_escape`
- **C9.2** 错误类型：缺少参数为 `invalid_args`；文件不存在或执行失败为 `execution`；越界或命令被禁止为 `permission`。`tests/test_coding_tools.py::TestCodingTools::test_read_file_missing`、`tests/test_coding_tools.py::TestCodingTools::test_read_file_missing_arg`
- **C9.3** `read_file` 不带分页参数时返回原文（不加行号）；超长时保留开头和结尾，中间标注「省略」，并设置 `truncated` 元数据。`tests/test_completion_gate.py::test_read_file_without_pagination_keeps_old_behavior`、`tests/test_coding_tools.py::TestCodingTools::test_read_file_truncate`
- **C9.4** `read_file` 分页：返回带真实行号的内容和 `total_lines`；没有读完时给出 `start_line=` 续读建议；只传 `start_line` 时读到文件末尾；只传 `end_line` 时从第 1 行开始；因字符上限截断时，续读建议按实际读到的最后一行计算。`tests/test_completion_gate.py::test_read_file_returns_requested_line_range`、`tests/test_completion_gate.py::test_read_file_character_limit_reports_actual_last_line`
- **C9.5** 分页参数非法（0、负数、结束行小于开始行、非整数）时返回 `invalid_args`。`tests/test_completion_gate.py::test_read_file_rejects_invalid_line_range`
- **C9.6** `list_dir` 输出带 `[dir]` 和 `[file]` 前缀；`glob` 支持 `**`；`grep` 输出 `path:line:` 格式，支持正则表达式。`tests/test_coding_tools.py::TestCodingTools::test_list_dir`、`tests/test_coding_tools.py::TestCodingTools::test_grep_regex`
- **C9.7** `write_file`：自动创建父目录，可以覆盖已有文件，允许空内容；缺少 `path` 或 `content` 时返回 `invalid_args`。`tests/test_coding_write_tools.py::TestWriteFile::test_write_creates_parent_dirs`、`tests/test_coding_write_tools.py::TestWriteFile::test_write_empty_content`
- **C9.8** `edit_file` 只替换唯一匹配：找不到时提示「找不到」，多处匹配时提示「不唯一」，文件不存在时报错，这三种都是 `execution` 错误；缺少 `old_text` 时返回 `invalid_args`。`tests/test_coding_write_tools.py::TestEditFile::test_edit_old_text_multiple`、`tests/test_coding_write_tools.py::TestEditFile::test_edit_old_text_not_found`
- **C9.9** `run_bash` 的命令允许列表只有 `pytest`、`python`、`npm`、`pip`、`git`，按程序文件名匹配（支持完整路径和 `.exe`）；不在列表中的命令返回 `permission` 错误。`tests/test_run_bash.py::TestRunBashPermission::test_allowed_commands_list`、`tests/test_run_bash.py::TestWhitelistCheck::test_python_full_path_in_whitelist`
- **C9.10** `run_bash` 拒绝 shell 元字符，返回 `permission` 错误：`|`、`;`、`&&`、`||`、`>`、`<`、`$`、反引号、`(`、`)`；空命令返回 `invalid_args`。`tests/test_run_bash.py::TestRunBashExecute::test_redirect_rejected`、`tests/test_run_bash.py::TestRunBashExecute::test_empty_command`
- **C9.11** 退出码不为 0 不算工具错误，只记录在 `exit_code` 元数据中；元数据带 `command`；输出超长时截断；超时和找不到命令都是 `execution` 错误。`tests/test_run_bash.py::TestRunBashExecute::test_non_zero_exit_code_not_error`、`tests/test_run_bash.py::TestRunBashTimeout::test_timeout_handled`
- **C9.12** `run_test` 与 `run_bash` 使用完全相同的允许列表和元字符规则。`tests/test_run_bash.py::TestRunTestAlias::test_run_test_rejects_metachars`
- **C9.13** `recall_history`：BM25 检索，中文按两字切分匹配；命中结果标注「第 N 轮」、会话 id 和文件时间；没有命中或没有会话时返回「没找到」提示，不算错误。`tests/test_clip_and_recall.py::TestRecallHistory::test_search_chinese_keyword`、`tests/test_memory_compaction_resilience.py::TestCrossSessionRecall::test_hit_includes_session_id_and_file_time`
- **C9.14** `recall_history` 只扫描最近 10 个会话；分数相同时较新的会话排在前面；相邻的命中合并且不重复；跳过损坏行；输出不超过长度上限。`tests/test_memory_compaction_resilience.py::TestCrossSessionRecall::test_only_recent_ten_sessions_are_scanned`、`tests/test_memory_compaction_resilience.py::TestCrossSessionRecall::test_adjacent_hits_are_merged_without_duplicates`

## 10. 模型层

- **C10.1** 完成判定：带工具调用时不算完成；没有工具调用但有文本时算完成；文本为空并且没有工具调用时不算完成。`tests/test_agent_loop.py::TestModelAdapter::test_not_done_with_tool_calls`、`tests/test_agent_loop.py::TestModelAdapter::test_not_done_with_empty_content`
- **C10.2** 服务端没有返回用量时记为未知，`usage_complete` 为 False，不当作 0 累加；显式返回 0 才算完整计数。`tests/test_token_accounting.py::test_missing_usage_is_not_reported_as_zero`
- **C10.3** 上下文超限识别：状态码为 400 或 413，并且响应包含超限关键词（`context_length_exceeded`、`maximum context length`）时，抛出 `ContextLengthExceededError`；普通 400 仍然是 HTTP 错误。`tests/test_memory_compaction_resilience.py::TestContextLengthDetection::test_413_with_vllm_style_message_raises_domain_error`、`tests/test_memory_compaction_resilience.py::TestContextLengthDetection::test_plain_400_without_keyword_stays_http_error`
- **C10.4** 流式请求超限时，异常直接交给循环处理，不能改用未压缩的消息做非流式重发。`tests/test_memory_compaction_resilience.py::TestContextLengthDetection::test_streaming_adapter_propagates_context_overflow`
- **C10.5** 流式输出逐段转发，依次发出 `start`、`chunk`、`end`，并累计输入和输出 Token 以及首字延迟。`tests/test_agent_loop.py::TestModelAdapter::test_streaming_done_and_metrics`
- **C10.6** 展示控制：有未验证的修改时照常接收 token，但不发给界面（只有 `start(False)` 和 `end`）；验证通过后，或者没有启用完成验证时，正常流式展示。`tests/test_completion_gate.py::test_streamed_candidate_is_buffered_while_gate_is_open`、`tests/test_completion_gate.py::test_streamed_clean_task_publishes_normally`
- **C10.7** 只有一个工具并且 `tool_choice` 为 `required` 时，改为指定这个工具名；`auto` 保持不变；`parallel_tool_calls` 始终为 False。`tests/test_llm_client.py::test_single_required_tool_uses_named_choice`、`tests/test_llm_client.py::test_auto_tool_choice_is_preserved`
- **C10.8** v2 调整：模型连接失败时，先按 C11.2 的规则重试；重试用尽后停止任务，已经写入的用户消息保留，用量标记为不完整。v1 不做任何重试。`tests/test_textual_cli.py::test_textual_real_connection_failure_stops_task_without_retrying`

## 11. 模型网关（v2 新增）

- **C11.1** 支持 OpenAI 兼容接口和 Anthropic 接口，两者的结果都转换成同一种 `AgentResponse`，完成判定与 C10.1 一致。
- **C11.2** 还没收到任何回复内容时，遇到限流（429）、服务端错误（5xx）或连接错误，按退避间隔重试，次数和总等待时间都有上限；已经开始接收流式内容后出错，直接抛出异常。
- **C11.3** 其他 4xx 错误和上下文超限不重试；上下文超限交给循环按 C1.9 处理。
- **C11.4** 主模型重试用尽后，按配置切换到备用模型，并发出 `model_fallback` 事件说明切换原因；没有配置备用模型时按 C10.8 结束任务。
- **C11.5** 用量按模型分别累计；配置了单价时计算费用；用量缺失时费用记为未知。
- **C11.6** 密钥只从环境变量读取，日志、事件和错误信息中不出现密钥。

## 12. MCP 客户端（v2 新增）

- **C12.1** 按配置连接 stdio 和 HTTP 两种 MCP 服务器，工具以 `服务器名__工具名` 注册进工具集合，工具定义与服务器提供的一致。
- **C12.2** MCP 工具默认需要询问，可以用权限规则放行或拒绝。
- **C12.3** 服务器启动失败时，它的工具不注册，并记录失败原因；运行中断开时，调用返回 `execution` 错误，任务继续。
- **C12.4** MCP 工具的结果按统一回执格式返回，并受输出长度上限约束。

## 13. 文件检查点（v2 新增）

- **C13.1** 每个任务中，写工具第一次修改某个文件前保存这个文件的快照；新建的文件记录为「原本不存在」。
- **C13.2** 可以把这些文件恢复到任意一个任务开始前的状态；恢复前需要用户确认，恢复后原本不存在的文件被删除。
- **C13.3** 只覆盖 `write_file` 和 `edit_file` 修改过的文件，Shell 命令造成的修改不在范围内，恢复时明确提示这一点。
- **C13.4** 快照保存在 Profile 状态目录，不进入 Git，也不写入会话记录。

## 14. 只读子代理（v2 新增）

- **C14.1** `task` 工具在独立的上下文中运行一个只读 Profile，子代理不触发审批，只把最终结论文本作为工具回执交回主任务。
- **C14.2** 子代理不能再创建子代理；它的轮数预算独立，并且有上限，超出时把已有结论和超限说明一起交回。
- **C14.3** 主任务取消时，子代理随之取消，主任务得到「未执行」或「已取消」回执。
- **C14.4** 子代理的记录保存在主会话的子目录，不混入主会话记录。

## 15. 执行环境与 Docker 沙箱（v2 新增）

- **C15.1** 本机和 Docker 两种执行环境，对工具提供相同的接口和相同的错误类型。
- **C15.2** Docker 环境只挂载工作区，默认关闭网络；工作区路径限制在容器中同样生效。
- **C15.3** Profile 选择 Docker 但 Docker 不可用时，任务开始前报错，不会自动改用本机执行。

## 16. 运行中插话（v2 新增）

- **C16.1** 任务运行中追加的消息，在下一次请求模型前按提交顺序加入对话，并写入会话记录。
- **C16.2** 插话不打断正在执行的工具，也不改变当前工具批次的处理。
- **C16.3** 任务结束时还没送出的插话，作为下一个任务的输入保留。

## 17. LSP 集成（v2 新增）

- **C17.1** 提供诊断、跳转定义、查找引用三个只读工具，结果带文件路径和行号。
- **C17.2** 语言服务器没有安装或启动失败时，工具返回明确的 `execution` 错误，任务继续。
- **C17.3** 路径受工作区限制。

## 18. ACP 协议（v2 新增）

- **C18.1** 通过 stdio 提供 ACP 服务端：事件转为通知，审批转为客户端请求，客户端的决定原样交给任务。
- **C18.2** 对外内容与事件的宿主可见视图一致，不暴露原始工具参数。
- **C18.3** 客户端断开时，取消正在运行的任务。

## 未列入的 v1 测试

以下测试检查的是 v1 的内部接线、兼容代码或界面细节，v2 不按它们设计：

- 旧状态目录迁移：`tests/test_state_migration.py`
- 数据结构字段与默认值：`tests/test_engine_contracts.py`
- `@tool` 装饰器元数据与旧工具模块兼容：`tests/test_tool_manager.py::TestToolDecorator`、`test_register_legacy_module`、`test_get_permission_without_decorator`
- 组装接线：`tests/test_runtime_assembly.py` 的组件共享测试、`tests/test_runtime.py` 的注册表测试、`tests/test_agent_status.py::test_factory_enables_status_bar_by_default`、`tests/test_context_selector.py` 与 `tests/test_session_store.py::TestContextManagerWiring` 的接线测试
- 旧会话目录约定：`tests/test_session_store.py::TestSessionStore::test_sessions_dir_for`
- 计划模式提示词文本：`tests/test_plan_mode.py::TestPlanModeInjection`
- 内部辅助函数：`tests/test_clip_and_recall.py::TestClipText`，以及 `run_bash` 内部检查函数的单元测试
- 界面布局、按键、补全和显示文案：`tests/test_textual_cli.py` 的界面测试、`tests/test_cli_polish.py::TestSlashCompleter`、`tests/test_tui_profiles.py` 的 Profile 菜单测试、`tests/test_token_accounting.py::test_cost_and_status_report_missing_usage_and_window_source`
- 腾讯记忆服务的请求格式细节：`tests/test_tencent_memory.py` 中检查 URL、请求头和请求体映射的测试
