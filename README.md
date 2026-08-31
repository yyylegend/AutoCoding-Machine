# AutoCoding Machine

一个面向代码库任务的轻量 Agent：模型通过工具读取、搜索、编辑并验证代码，CLI 负责交互，Runtime 负责组装循环、权限、上下文、记忆与工具。

这个仓库刻意只保留一条主线：**Coding Agent + CLI**。它不依赖前端、浏览器自动化、数据库、Redis 或任务队列。

## 核心能力

- Tool-calling 循环：模型决定读取、搜索、编辑或执行测试。
- 工作区沙箱：文件操作不能越过指定项目目录。
- 权限分级：只读操作自动执行，写入与命令执行需要确认。
- 上下文管理：按 Token 预算压缩长对话。
- 会话与记忆：JSONL 会话记录，加上项目和用户级 Markdown 记忆。
- Plan Mode：规划阶段阻止写工具，避免边想边改。
- 扩展注册：Profile 通过 Runtime Registry 注册工具、检查与注入。

## 快速开始

```powershell
uv sync
Copy-Item .env.example .env
uv run python -m src.profiles.coding.cli
```

在 `.env` 中填写 OpenAI-compatible 模型的 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY`。

项目使用 `pyproject.toml` 声明依赖，使用 `uv.lock` 锁定可复现的依赖版本；新增或删除依赖时使用 `uv add` 或 `uv remove`。

## 测试

```powershell
uv run pytest
```

## 架构

```text
CLI
 └─ Runtime Factory
     ├─ Coding Profile
     ├─ Machine Loop
     ├─ Context / Session / Memory
     ├─ Permission / Guard / Hooks
     └─ Coding Tools
          ├─ read / list / glob / grep
          ├─ write / edit
          ├─ run_test / run_bash
          └─ skills / memory / history
```

模块职责与调用流程见 [`docs/architecture.md`](docs/architecture.md)。

## 安全约定

- `.env`、`.git` 与 `.autocoding` 默认禁止被写工具修改。
- 未注册工具默认拒绝。
- Shell 工具使用白名单、固定工作目录与超时限制。
- 密钥只放在本地 `.env`，不要提交。

## License

见 [LICENSE](LICENSE)。
