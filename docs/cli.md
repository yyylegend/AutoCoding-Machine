# Textual CLI 使用指南

[返回首页](../README.md) · [Profile 配置](profiles.md) · [Harness 架构](architecture.md)

## 启动

在你希望处理的项目目录启动；该目录就是工具的工作区。

```powershell
uv run python -m auto_coding_machine
uv run python -m auto_coding_machine --profile review
uv run python -m auto_coding_machine --resume
```

界面包含滚动对话区、固定输入区、当前模型与预算状态。宽终端显示会话侧栏和原有渐变 Logo，窄终端使用紧凑品牌标题；Ctrl+B 可以显示侧栏。

工具操作以可展开的状态行显示，详情使用脱敏参数。需要权限的操作会显示确认弹窗，默认焦点停在拒绝按钮；批准需要明确操作。关闭完成验证不会关闭权限检查。

## 输入与快捷键

| 操作 | 按键 |
| --- | --- |
| 发送任务或命令 | Enter，也可以点击发送 |
| 插入换行 | Alt+Enter 或 Shift+Enter |
| 命令、Profile、会话和技能补全 | 输入 `/` 查看提示；↑ / ↓ 选择，Tab 或 Enter 确认 |
| 查看上一条或下一条输入 | Ctrl+↑ / Ctrl+↓ |
| 取消当前任务 | Ctrl+C，或点击取消 |
| 显示或隐藏会话侧栏 | Ctrl+B |
| 查看帮助 | F1 或 `/help` |
| 切换完成验证 | F2，或 `/verify on|off` |
| 拒绝权限请求 | Esc，或点击拒绝 |
| 退出 | Ctrl+Q 或 `/quit` |

取消是协作式的：工具或网络请求已经开始时，需要等待它到达检查点；已经发生的修改不会撤回。正在执行任务时不能切换 Profile、会话或完成验证，退出也需等待当前操作结束。

## 完成验证：默认关闭

完成验证属于实验功能，用来检查代码净修改后有没有新鲜的测试或构建证据。它不保证所有用户需求已经完成。

Textual CLI 每次启动默认关闭，界面会明确显示当前状态。需要时使用侧栏开关、F2 或命令：

```text
/verify on
/verify off
```

也可以从命令行开启：

```powershell
uv run python -m auto_coding_machine --verify-on-stop
uv run python -m auto_coding_machine --no-verify-on-stop
```

CLI 的显式选择优先于环境变量与 Profile 配置。通过 Python 组装 Harness 的调用方继续使用原来的 Profile/环境变量约定。此功能仅适用于 Coding；Review 和 Companion 不启用。

## 会话与常用命令

- `/profile` 打开配置选择；`/profile review` 或 YAML 路径切换配置。切换会打开新会话，旧会话保留。
- `/sessions` 列出当前 Profile 的历史会话；`/resume <id>` 恢复；`/new` 新建。
- `/plan` 进入只读计划模式；`/exit` 退出计划模式。
- `/clear` 清空当前视图，保留原始 JSONL；`/compact` 经确认压缩上下文视图。
- `/status` 查看会话、模型窗口和工具信息；`/cost` 查看本次任务已报告的用量；`/prompt` 查看每条请求消息的角色和字符数。
- `/skills`、`/skill <名称>` 查看或加载技能；`/memory` 查看长期记忆；`/prompt` 查看请求消息结构。

模型或组件异常会停止当前任务，界面报告失败类别。检查配置后使用 `/new` 或 `/resume`；界面不会自动重发失败的模型请求。会话写入失败时，工具可能已经生效，请先检查真实文件和会话记录。

## 界面参考与实现

界面交互参考 [Pi 的终端使用方式](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md) 和 [Qwen Code 的启动信息布局](https://github.com/QwenLM/qwen-code/blob/main/packages/cli/src/ui/components/Header.tsx)。实现使用 Textual 原生组件，后台线程遵循 [Textual Worker 约定](https://textual.textualize.io/guide/workers/)。

OpenCode Go 的流式调用发送 AutoCoding 自身 User-Agent 和当前会话 ID，符合其[其他客户端接入要求](https://opencode.ai/docs/go/#where-can-i-use-it)。这项处理位于终端 Adapter；Engine 不依赖该服务。
