# AutoCoding Machine

轻量、模块化、可组合的 Python Agent Framework。开发者可以接入自己的模型、工具和业务规则，复用执行循环、权限、会话、上下文与运行事件。

Coding CLI 是框架的实际应用：你用自然语言描述需求，它可以读代码、修改文件、运行测试，也可以切换成只读审查或陪伴聊天。产品定位、最终形态和能力进度见[产品总览](docs/product-overview.md)；在自己的程序中使用框架，从[快速入门](docs/harness-quickstart.md)开始。

例如，你可以直接输入：

> 帮我看一下这个项目，先解释代码怎么运行，暂时不要修改。

## 它能做什么？

内置三种模式。项目里把一套模式配置叫作 **Profile**，可以理解成“角色设定 + 能用的工具”。

| 模式 | 适合做什么 |
| --- | --- |
| `coding`（默认） | 写功能、修 Bug。修改文件和执行命令时会请求确认；可选开启修改后的完成验证门 |
| `review` | 只看代码、找问题、给建议，不修改文件、不执行命令 |
| `companion` | 陪伴聊天，可以记录你提供的偏好，不使用代码和命令工具 |

Coding 的“完成证据门”是实验性的修改验证检查：发现已跟踪的代码净修改，却没有修改后的成功验证时，会提醒助手补做；仍未验证则明确标注。Textual CLI 默认关闭，通过界面开关、`/verify on|off` 或 `--verify-on-stop` 设置；Python 调用方使用 Profile 和环境变量约定。它不保证测试覆盖了改动，也不代表需求全部完成；无净变化或只有文档修改时可直接交付。具体范围见[配置指南](docs/profiles.md#自定义-profile)和[架构说明](docs/architecture.md#完成证据门)。

聊天记录会保存，之后可以接着聊。不同 Profile 分开保存会话和记忆，默认 Coding 的用户偏好仍沿用原来的全局记忆文件。

每个 Profile 的会话和输入历史都放在 `.autocoding/profiles/<profile-name>/` 下；详细运行记录默认关闭，评测时在 YAML 中设置 `trace_enabled: true` 才会开启。

## 怎么启动？

需要先安装 **Python 3.11 或更新版本**和 **uv**。以下命令在项目目录中运行。

**1. 安装项目依赖。**

```powershell
uv sync
```

**2. 配置模型。** 首次使用时复制配置模板；如果已经有 `.env`，跳过复制。

```powershell
Copy-Item .env.example .env
```

打开 `.env`，填写这三项：

- `LLM_BASE_URL`：模型服务的 API 地址。
- `LLM_MODEL`：要使用的模型名称。
- `LLM_API_KEY`：访问模型服务的密钥。

需要使用兼容 OpenAI 接口的模型服务。密钥留在本地，不要提交到仓库。

**3. 启动助手。**

```powershell
uv run python -m auto_coding_machine
```

启动时所在的目录就是它处理文件的工作区。进入界面后直接打字即可：**Enter 发送，Alt+Enter 换行**。

Textual 界面提供滚动对话区、固定多行输入区、会话侧栏和权限弹窗。宽终端保留 AUTOCODING MACHINE 渐变 Logo，窄终端使用紧凑标题；工具详情可以展开。Ctrl+B 显示或隐藏会话侧栏，Ctrl+Q 退出。完整操作见 [CLI 使用指南](docs/cli.md)。

`/status` 可查看模型窗口及来源：手动配置、服务端报告或未知。token 在请求前只能估算（含工具定义）；上次请求的输入量以服务端 `usage` 为准。`/cost` 显示本次任务已报告的用量，报告缺失时会明确提示，详见[计数与窗口说明](docs/profiles.md#token-用量与模型窗口)。

## 常用命令

这些命令是在助手界面里输入的：

| 命令 | 作用 |
| --- | --- |
| `/profile` | 查看有哪些配置 |
| `/profile review` | 切换成只读审查；换成 `coding` 或 `companion` 就能切换其他模式 |
| `/sessions` | 查看当前模式下的聊天记录 |
| `/resume <会话ID>` | 继续某一次聊天，ID 可以从记录列表中找到 |
| `/memory` | 查看保存的长期记忆 |
| `/verify on` / `/verify off` | 开启或关闭实验性的完成验证；默认关闭 |
| `/help` | 查看全部命令 |
| `/quit` | 退出 |

输入 `/` 会弹出命令提示，Tab 可以补全。**切换 Profile 会开始新对话，旧对话仍然保留。** 任务执行中想停下来，先按 Ctrl+C。

## 想改成自己的角色？

内置预设只有 `coding`、`review`、`companion`。教学示例放在 [examples/profiles](examples/profiles/)，不会自动出现在切换列表里。

想创建自己的角色，先复制示例，再修改 `name` 和 `prompt`：

```powershell
Copy-Item examples/profiles/companion.yaml profile_configs/my-companion.yaml
```

在助手界面中加载：

```text
/profile profile_configs/my-companion.yaml
```

`profile_configs/` 中的 YAML 会出现在列表里，自定义配置用文件路径切换。没有启用或发现 Skills 时，模型不会收到技能搜索和加载工具。更多字段和记忆位置见[配置指南](docs/profiles.md)。

## 想了解代码？

- [产品总览](docs/product-overview.md)：目标用户、最终形态、能力模块、工作流和产品边界。
- [在代码里使用 Harness](docs/harness-quickstart.md)：从一个最小例子开始，了解会话、任务和结果。
- [Harness Cookbook](docs/harness-cookbook.md)：按实际任务学习模型、工具、权限、会话和可选记忆的接法。
- [Microsoft Agent Framework 对照阅读](docs/microsoft-agent-framework.md)：用一个任务看懂 Agent、Harness、Session 与 Workflow，并对照本项目。
- [当前架构](docs/architecture.md)：现在有哪些模块，分别负责什么。
- [交互式架构图](docs/diagrams/README.md)：下载后用浏览器打开，查看整体结构和完成验证流程。
- [后续需求与计划](docs/plans/README.md)：当前主线聚焦可组合 Harness 与适配器；暂缓功能和历史方案见索引。

已完成或暂缓的设计记录归档在 [docs/archive](docs/archive/)，需要了解历史取舍时再看。

修改代码后，可以运行检查：

```powershell
uv run pytest
```

测试包含模拟模型的流程验证，不代表真实模型的任务成功率。Qwen GGUF 专项优化还在后续计划中。

开源协议：[MIT](LICENSE)。
