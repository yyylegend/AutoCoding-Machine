"""完成证据门（CompletionGate V2）：用文件净变化 + 验证证据判断任务是否真的完成。

【大白话】
  模型说"我做完了"，要不要信？不要直接信。
  这个门只认两条硬证据：

    证据 1 —— 文件净变化：
      任务过程中被 write_file / edit_file 碰过的路径，
      到了宣布完成那一刻，内容和任务开始前比有没有"真实留下来"的变化。
      临时文件建了又删、文件改了又改回原样，都不算净变化。

    证据 2 —— 新鲜验证：
      最后一次真实修改之后，有没有跑过成功的测试 / lint / build 类命令。
      改完代码才跑的测试才算数；先跑测试后改代码，测试是过期的。

  两条都过关 → 放行最终回答；
  有净变化但没验证 → 把候选回答先存起来（pending），让模型继续去验证；
  连续两次都没证据 → 放弃重试，但把候选回答连同"未验证"标记一起交给用户，
  用户已经等来了一版实质回答，不能因为验证没过就弄丢。

【为什么放 Coding Profile 而不是 Engine】
  "什么算修改、什么算验证"是 Coding 专属策略（write_file / pytest 这些名字
  只在 Coding 世界里存在）。Engine 里的 MachineLoop 只认 evaluate() 这一个口子，
  不读门内部的任何计数器 —— 换个 Profile 可以有完全不同的完成策略。

【不变量】
  - 这个类只观察和判断，不执行任何工具、不修改任何文件；
  - 所有状态只活在当前进程内存里，不落盘、不进 JSONL；
  - 任何文件读取失败都按"保守出事"处理：当成有变化、要求验证，绝不静默放行。
"""

import hashlib
import re
from dataclasses import dataclass

from src.profiles.coding.sandbox import WorkspaceSandbox

# 读大文件时按块计算 hash，每次最多读 64KB，不把整个文件怼进内存
_HASH_BLOCK_SIZE = 64 * 1024


@dataclass(frozen=True)
class FileState:
    """一个路径在某个时刻的快照：文件存在吗 + 内容指纹。"""

    exists: bool
    sha256: str | None


@dataclass(frozen=True)
class CompletionDecision:
    """完成判断结果。MachineLoop 只消费这个，不读 Gate 内部状态。

    字段：
      action               — "accept"（放行）/ "continue"（退回去验证）/ "fail"（放弃重试）
      final_response       — Gate 裁定的唯一最终回答。
                             accept 时是 candidate（或 candidate+footer）；
                             fail 时是 candidate + 未验证 footer。
                             CLI 只展示这一个字符串，不自己拼。
      continuation_message — action=continue 时给模型看的验证提示。
                             运行时临时消息，绝不能写进 Session JSONL。
      reason               — 人话说明为什么做出这个判断（给 Hook / 日志用）
      changed_paths        — 本轮发现的文件净变化路径列表
      candidate_version    — 候选回答绑定到的有效修改版本
      validation_version   — 当前验证证据的版本
    """

    action: str
    final_response: str
    continuation_message: str = ""
    reason: str = ""
    changed_paths: tuple = ()
    candidate_version: int = 0
    validation_version: int = 0


class CompletionGate:
    """跟踪一个任务内的文件基线、修改版本和验证证据，裁决任务能否完成。"""

    # 算"净变化"时要盯的写工具
    MUTATION_TOOLS = ("write_file", "edit_file")

    # run_bash 命令里出现这些词（按完整 token 匹配，不是子串！）且退出码为 0，
    # 才算一次验证。token 级匹配是为了防误伤：
    #   "git checkout" 的 token 是 {git, checkout}，不含 "check"，不会误判成验证；
    #   而 "git status" / "pwd" / "ls" 这类命令天然不在这个表里。
    # 这些后缀 / 文件名算"纯文档"：只有它们变化时不要求跑代码测试
    DOC_SUFFIXES = (".md", ".rst", ".txt")
    DOC_NAMES = ("license", "changelog", "notice")

    # 连续这么多次"有净变化但没证据"就放弃重试（防无限 continuation）
    def __init__(self, sandbox: WorkspaceSandbox, max_rejections: int = 2):
        self.sandbox = sandbox
        self.max_rejections = max_rejections
        self.start_task()

    def start_task(self) -> None:
        """开始一个新用户任务：清空上一任务的所有状态（FR-1）。"""
        # 路径（workspace 相对路径）→ 首次写入前的基线快照
        self._baseline: dict = {}
        # 首次快照读取失败的路径：无法证明未变化时必须保守拦截
        self._unknown_baselines: set = set()
        # 路径 → 该路径最后一次成功修改时的全局版本号
        self._last_mutation: dict = {}
        # 全局单调递增版本号：每成功修改一次 +1
        self._mutation_version = 0
        # 最近一次成功验证发生时的全局版本号；0 = 还没验证过
        self._validation_version = 0
        # 候选回答与它绑定的版本（验证 continuation 期间保存，见 FR-19/21）
        self._pending_response: str | None = None
        self._pending_version: int | None = None
        # 最近一次写工具（before_tool）要碰的路径，给 after_tool 做回落
        self._pending_path: str | None = None
        # "有净变化但没证据"的连续计数
        self._rejection_count = 0

    # ================================================================
    # Hook 入口：直接注册到 HookManager（factory 里 hooks.on(...)）
    # ================================================================

    def before_tool(self, tool_name: str, arguments: dict | None = None, **_) -> None:
        """pre_tool Hook：写工具执行前，先把目标路径的基线快照存下来（FR-2）。

        为什么在执行前拍快照：工具可能失败（部分写入、被用户拒绝），
        但文件可能已经被碰过了。以"首次触碰前"的状态为基线才可靠。
        """
        if tool_name not in self.MUTATION_TOOLS:
            return
        arguments = arguments or {}
        path = arguments.get("path")
        if not path:
            return
        rel = self._normalize(path)
        if rel is None:
            # 越界或非法路径：不读它（沙箱本来也会拦住），直接不管
            return
        if rel not in self._baseline:
            # 只在第一次碰到这个路径时拍快照，之后的修改不影响基线
            state = self._read_state(rel)
            if state is not None:
                self._baseline[rel] = state
            else:
                self._unknown_baselines.add(rel)
        # 记住"最近一次写工具要碰的路径"：post_tool 的 metadata 万一没带 path，
        # 就用这个回落（before_tool / after_tool 是紧接着的一对）
        self._pending_path = rel

    def after_tool(self, tool_name: str, error: bool = False,
                   result_metadata: dict | None = None, **_) -> None:
        """post_tool Hook：记录修改版本（FR-4）和验证证据（FR-12/13）。"""
        metadata = result_metadata or {}

        # ---- 成功的写操作：推进版本号 ----
        if tool_name in self.MUTATION_TOOLS:
            # 即使工具报错，也可能已经部分落盘。先推进版本，
            # 最终再由净变化比较排除“失败且什么也没改”。
            self._mutation_version += 1
            # 路径优先取 metadata（两个写工具都会回传），
            # 取不到就回落到 before_tool 记住的那条
            rel = self._normalize(metadata.get("path")) or self._pending_path
            if rel is not None:
                self._last_mutation[rel] = self._mutation_version
            return

        # ---- 成功的验证命令：记录"验证发生时的版本"（FR-14/15）----
        if self._is_successful_validation(tool_name, error, metadata):
            self._validation_version = self._mutation_version

    # ================================================================
    # 完成判断：MachineLoop 唯一会调的口子
    # ================================================================

    def evaluate(self, candidate_response: str) -> CompletionDecision:
        """模型给出候选最终回答时，判断放行、退回验证还是放弃。

        candidate_response — 模型本轮给出的最终回答文本。
                             门会把它保存起来，验证通过后原样交还用户（FR-23），
                             不允许模型在验证回执里悄悄顶替原回答。
        """
        candidate = candidate_response or ""
        changed = self._compute_changed_paths()
        # 有效修改版本 = 所有净变化路径里"最后修改版本"的最大值（FR-14）
        effective = max(
            (self._last_mutation.get(rel, 0) for rel in changed), default=0
        )

        # ---- 情况 1：没有净变化（临时文件已删 / 改动已还原 / 纯讲解）----
        # 门是干净的，直接放行，不需要任何测试（FR-7/8/9）
        if not changed:
            self._pending_response = None
            self._pending_version = None
            self._rejection_count = 0
            return CompletionDecision(
                action="accept",
                final_response=candidate,
                reason="没有文件净变化",
                changed_paths=(),
                validation_version=self._validation_version,
            )

        # ---- 情况 2：净变化全是文档 —— 跳过代码式验证（FR-10）----
        if all(self._is_doc(rel) for rel in changed):
            self._pending_response = None
            self._pending_version = None
            self._rejection_count = 0
            return CompletionDecision(
                action="accept",
                final_response=candidate + self._doc_footer(changed),
                reason="只有文档类文件变化，跳过代码验证",
                changed_paths=tuple(changed),
                candidate_version=effective,
                validation_version=self._validation_version,
            )

        # ---- 情况 3：有真实代码净变化，且验证证据是新鲜的 ----
        if self._validation_version >= effective:
            self._rejection_count = 0
            # 如果之前存了一个候选回答、且它绑定的版本没变，
            # 说明本轮 candidate 只是"验证回执"——
            # 用原来的候选回答作答，不让回执顶替实质内容（FR-23）
            if self._pending_response is not None and self._pending_version == effective:
                final = self._pending_response + self._verified_footer(changed)
                self._pending_response = None
                self._pending_version = None
                return CompletionDecision(
                    action="accept",
                    final_response=final,
                    reason="验证通过，复用原候选回答",
                    changed_paths=tuple(changed),
                    candidate_version=effective,
                    validation_version=self._validation_version,
                )
            self._pending_response = None
            self._pending_version = None
            return CompletionDecision(
                action="accept",
                final_response=candidate,
                reason="最后一次修改后已有成功验证",
                changed_paths=tuple(changed),
                candidate_version=effective,
                validation_version=self._validation_version,
            )

        # ---- 情况 4：有净变化但证据缺失或过期 —— 退回去验证（FR-21）----
        # 先确定 fail 时该交付哪份回答：
        #   旧候选还绑定在当前版本上 → 交付旧候选（它才是"原回答"）；
        #   版本已经变了（新一轮修改）→ 旧候选过期，交付本轮 candidate（FR-24）
        pending_alive = (
            self._pending_response is not None
            and self._pending_version == effective
        )
        final_if_fail = (
            self._pending_response if pending_alive else candidate
        ) + self._unverified_footer(changed)

        # 无论走 continue 还是 fail，本轮 candidate 都成为新的 pending，
        # 绑定当前有效版本（验证通过后要复用的就是它）
        self._pending_response = candidate
        self._pending_version = effective
        self._rejection_count += 1

        if self._rejection_count < self.max_rejections:
            return CompletionDecision(
                action="continue",
                final_response=candidate,
                continuation_message=self._nudge(changed),
                reason="最后一次修改之后没有成功验证",
                changed_paths=tuple(changed),
                candidate_version=effective,
                validation_version=self._validation_version,
            )

        # 连续两次没证据：放弃重试，但把候选回答 + 未验证标记一起交付（FR-25）
        return CompletionDecision(
            action="fail",
            final_response=final_if_fail,
            reason="连续两次缺少修改后的验证证据，停止重试",
            changed_paths=tuple(changed),
            candidate_version=effective,
            validation_version=self._validation_version,
        )

    def should_publish_stream(self) -> bool:
        """流式展示策略：候选回答该不该实时显示给用户。

        返回 True = 可以正常流式展示（干净任务，回答大概率直接生效）；
        返回 False = 静默缓冲（有未验证的修改或已有候选在等验证，
        这时候的文本大概率要被 Gate 打回去，先别显示给用户）。

        只回答策略，不执行副作用（接口约束）。
        用"修改版本 > 验证版本"做廉价判断，避免每次流式都去读文件。
        """
        return (
            self._pending_response is None
            and self._mutation_version <= self._validation_version
        )

    def take_pending_final(self) -> str | None:
        """取走待交付的候选回答（带未验证 footer），没有就返回 None。

        谁用：MachineLoop 在 max_turns 耗尽时调用——
        有候选回答就交付它，不能只返回一个冷冰冰的 max_turns（FR-26）。
        """
        if self._pending_response is None:
            return None
        changed = self._compute_changed_paths()
        effective = max(
            (self._last_mutation.get(rel, 0) for rel in changed), default=0
        )
        if self._pending_version != effective:
            return None
        return self._pending_response + self._unverified_footer(changed)

    # ================================================================
    # 内部方法：文件状态、净变化、分类判断
    # ================================================================

    def _normalize(self, path) -> str | None:
        """把工具参数里的路径转成 workspace 相对路径（统一键名）。

        返回 None = 路径越界或非法，一律不碰（NFR-3）。
        """
        if not path:
            return None
        full = self.sandbox.resolve(str(path))
        if full is None:
            return None
        return self.sandbox.relpath(full)

    def _read_state(self, rel: str) -> FileState | None:
        """读取一个路径当前的状态快照。读失败返回 None（调用方保守处理）。"""
        full = self.sandbox.resolve(rel)
        if full is None:
            return None
        try:
            if not full.is_file():
                return FileState(exists=False, sha256=None)
            return FileState(exists=True, sha256=self._hash_file(full))
        except OSError:
            # 文件被外部进程动过、暂时不可读等：让调用方保守处理（NFR-4）
            return None

    @staticmethod
    def _hash_file(path) -> str:
        """分块计算文件内容的 SHA-256（NFR-2：不把大文件整份读进内存）。"""
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                block = f.read(_HASH_BLOCK_SIZE)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    def _compute_changed_paths(self) -> list:
        """把所有跟踪路径的当前状态与基线比较，得到净变化列表（FR-5/6/7/8）。

        判定规则：
          - 基线不存在、现在也不存在     → 没变化（临时文件建了又删，FR-7）
          - 存在性变了                   → 有变化（新建留下的文件 / 被删掉的旧文件）
          - 都存在但 hash 不同           → 有变化
          - 都存在且 hash 相同           → 没变化（改了又改回去，FR-8）
          - 读不到当前状态               → 保守当成有变化（NFR-4）
        """
        changed = list(self._unknown_baselines)
        for rel, base in self._baseline.items():
            current = self._read_state(rel)
            if current is None:
                changed.append(rel)
            elif base.exists != current.exists:
                changed.append(rel)
            elif current.exists and base.sha256 != current.sha256:
                changed.append(rel)
        return changed

    def _is_successful_validation(self, tool_name: str, error: bool,
                                  metadata: dict) -> bool:
        """判断一次工具执行算不算"成功验证"（FR-12/13、EC-5/6）。

        run_test：工具没报错且 exit_code == 0。
        run_bash：同上，且命令按 token 级白名单被识别为测试 / lint / build 类。
        """
        if error or metadata.get("exit_code") != 0:
            return False
        if tool_name == "run_test":
            return True
        if tool_name != "run_bash":
            return False
        command = str(metadata.get("command") or "")
        tokens = re.findall(r"[a-zA-Z0-9_./\\-]+", command.lower())
        if not tokens:
            return False

        def executable(value):
            return value.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")

        tokens[0] = executable(tokens[0])
        if tokens[:2] == ["uv", "run"]:
            tokens = tokens[2:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
        if len(tokens) >= 3 and executable(tokens[0]) in {"python", "python3", "py"} and tokens[1] == "-m":
            tokens = tokens[2:]
        if not tokens:
            return False

        tool = executable(tokens[0])
        if tool in {
            "pytest", "unittest", "nose", "tox", "ruff", "flake8", "pylint",
            "mypy", "pyright", "tsc", "eslint", "coverage", "compileall",
        }:
            return True
        if tool == "git":
            return len(tokens) >= 3 and tokens[1] == "diff" and "--check" in tokens[2:]
        if tool in {"npm", "pnpm", "yarn", "bun"}:
            scripts = {"test", "lint", "build", "check", "typecheck", "type-check"}
            return any(token in scripts for token in tokens[1:3])
        if tool in {"cargo", "go", "dotnet", "make"}:
            return len(tokens) >= 2 and tokens[1] in {"test", "check", "build"}
        return False

    def _is_doc(self, rel: str) -> bool:
        """判断一个路径是不是纯文档（.md / .rst / .txt / LICENSE 类）。"""
        name = rel.rsplit("/", 1)[-1].lower()
        if name.endswith(self.DOC_SUFFIXES):
            return True
        return name.startswith(self.DOC_NAMES)

    # ================================================================
    # 各种给模型 / 用户看的小文本
    # ================================================================

    @staticmethod
    def _nudge(changed: list) -> str:
        """验证提示（synthetic verification nudge）：给模型的继续验证指令。

        注意：这是内部脚手架，MachineLoop 只把它放进运行时消息列表，
        绝不写进 Session JSONL（FR-22）。
        """
        paths = "、".join(changed[:5])
        return (
            "[完成验证] 系统检测到你修改了文件（" + paths + "），"
            "但最后一次修改之后没有成功的验证证据。"
            "请运行相关测试或检查命令（如 pytest），验证通过后再给出最终回复。"
        )

    @staticmethod
    def _verified_footer(changed: list) -> str:
        """验证通过时附在最终回答末尾的一行确认。"""
        return "\n\n---\n✅ 已验证：修改后的测试/检查通过（" + "、".join(changed[:5]) + "）"

    @staticmethod
    def _unverified_footer(changed: list) -> str:
        """验证缺失时附在最终回答末尾的紧凑标记（FR-25）。"""
        return (
            "\n\n---\n⚠ 未验证：以下文件修改后未运行成功验证（"
            + "、".join(changed[:5]) + "），结果可能不完整"
        )

    @staticmethod
    def _doc_footer(changed: list) -> str:
        """纯文档变化时的标记（EC-10）：跳过了代码测试，如实告知。"""
        return "\n\n---\n📄 文档变更：" + "、".join(changed[:5]) + "（未运行代码测试）"
