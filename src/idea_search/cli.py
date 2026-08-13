"""Command-line entry point for idea-search."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import ConfigError, Settings
from .github import GitHubClient
from .llm import LLMClient, LLMError
from .models import SearchResult
from .search import SearchService
from .storage import HistoryStore


def _print_result(result: SearchResult) -> None:
    print("搜索策略:")
    if result.plan.keywords:
        print("  关键词: " + ", ".join(result.plan.keywords))
    for q in result.plan.queries:
        print(f"  查询: {q}")
    if result.plan.languages:
        print("  语言: " + ", ".join(result.plan.languages))
    if result.plan.notes:
        print(f"  备注: {result.plan.notes}")
    print()
    print("匹配项目:")
    for i, repo in enumerate(result.repositories, 1):
        print(f"{i}. {repo.full_name} ⭐{repo.stars:,} [{repo.score}/100]")
        if repo.reason:
            print(f"   {repo.reason}")
    print()
    if result.summary:
        print("总结:")
        print(result.summary)


async def _run_text(
    idea: str,
    language_filter: str | None,
    settings: Settings,
) -> SearchResult:
    llm = LLMClient(settings)
    github = GitHubClient(settings)
    try:
        service = SearchService(llm, github)
        return await service.search(
            idea,
            language_filter=language_filter,
            on_progress=lambda m: print(m, file=sys.stderr),
        )
    finally:
        await llm.close()
        await github.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="idea-search",
        description="用自然语言描述一个想法，在 GitHub 上寻找已有的匹配项目。",
    )
    parser.add_argument(
        "idea",
        nargs="?",
        default=None,
        help="你的想法，例如：自托管的个人知识库 + AI 问答（省略时以 TUI 模式启动，可自行输入）",
    )
    parser.add_argument(
        "--text", action="store_true", help="以纯文本模式输出结果（不启动 TUI）"
    )
    parser.add_argument(
        "--lang", default=None, help="按语言过滤结果，例如 python"
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        settings.llm_api_key  # raises ConfigError if missing
    except ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if not args.text:
        try:
            from .tui import run_tui
        except ImportError:
            print("错误: 无法导入 TUI 模块，已回退到文本模式。", file=sys.stderr)
            args.text = True
        else:
            try:
                return run_tui(args.idea, language_filter=args.lang)
            except KeyboardInterrupt:
                return 0

    if args.idea is None:
        print("错误: --text 模式需要提供想法描述。", file=sys.stderr)
        parser.print_usage(file=sys.stderr)
        return 1

    try:
        result = asyncio.run(_run_text(args.idea, args.lang, settings))
    except ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0

    try:
        HistoryStore().add(result)
    except Exception as exc:
        print(f"警告: 历史记录保存失败: {exc}", file=sys.stderr)

    _print_result(result)
    return 0