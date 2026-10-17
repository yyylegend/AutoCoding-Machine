"""AutoCoding Machine 命令行入口：解析参数并启动 Textual 界面。"""


def main():
    """共享命令行入口；新旧启动命令都走这里。"""
    import argparse

    parser = argparse.ArgumentParser(description="Coding Agent CLI")
    # nargs="?" 让 --resume 可以不带值：
    #   不写 --resume        → resume=None（新开会话）
    #   只写 --resume        → resume=""（恢复最近一次）
    #   写 --resume <id>     → resume="<id>"（恢复指定会话）
    parser.add_argument(
        "--resume", nargs="?", const="", default=None, metavar="SESSION_ID",
        help="恢复会话：不带值恢复最近一次，带值恢复指定 id（/sessions 可查）",
    )
    parser.add_argument("--profile", default="coding", help="coding / review / companion 或自定义 YAML 路径")
    parser.add_argument("--verify-on-stop", action=argparse.BooleanOptionalAction, default=False,
                        help="开启或关闭实验性的完成验证；默认关闭")
    args = parser.parse_args()
    try:
        from auto_coding_machine.profiles.coding.tui.app import CodingApp
        CodingApp(resume=args.resume, profile=args.profile, verify_on_stop=args.verify_on_stop).run()
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
