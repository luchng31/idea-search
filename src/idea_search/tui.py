"""Textual TUI for idea-search.

Flow: input screen -> loading (live progress) -> results (list + detail),
with an error screen for configuration/search failures.
"""

from __future__ import annotations

import webbrowser

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    LoadingIndicator,
    OptionList,
    RichLog,
    Static,
    TextArea,
)
from textual.widgets._option_list import Option
from rich.text import Text

from .models import Repo, SearchResult, SearchPlan, repos_sorted
from .storage import HistoryEntry, HistoryStore

ACCENT = "#7aa2f7"
MUTED = "#565f89"
CHIP_BG = "#2a3b5c"
GREEN = "#9ece6a"
YELLOW = "#e0af68"
RED = "#f7768e"


def _score_color(score: int) -> str:
    if score >= 70:
        return GREEN
    if score >= 50:
        return YELLOW
    return RED


def _score_badge(score: int) -> Text:
    return Text(f" {score:3d} ", style=f"bold {_score_color(score)}")


def _score_bar(score: int) -> str:
    filled = max(0, min(10, round(score / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{_score_color(score)}]{bar}[/] [dim]{score}/100[/]"


def _chips(topics: list[str]) -> str:
    from rich.markup import escape

    return " ".join(f"[on {CHIP_BG}]{escape(t)}[/]" for t in topics[:8])


def _repo_detail_markup(repo: Repo) -> str:
    lines = [
        f"[bold {ACCENT}]{repo.full_name}[/]",
        f"[dim]{repo.url}[/]",
        "",
        repo.description or "[dim]（无描述）[/]",
        "",
    ]
    meta = f"语言: {repo.language or '未知'}    ⭐ {repo.stars:,}    🍴 {repo.forks:,}"
    if repo.updated_at:
        meta += f"    更新: {repo.updated_at[:10]}"
    lines.append(meta)
    if repo.topics:
        lines.append("")
        lines.append(_chips(repo.topics))
    lines += ["", _score_bar(repo.score)]
    if repo.reason:
        lines += ["", f"[dim]匹配理由:[/] {repo.reason}"]
    return "\n".join(lines)


_SLASH_COMMANDS: tuple[str, ...] = (
    "/history",
    "/clear-history",
    "/help",
    "/quit",
)

_SLASH_KEYS = ("up", "down", "enter", "escape", "tab")


class SlashTextArea(TextArea):
    async def _on_key(self, event: events.Key) -> None:
        screen = self.screen
        forward = getattr(screen, "handle_slash_key", None)
        if (
            event.key in _SLASH_KEYS
            and callable(forward)
            and forward(event.key) is True
        ):
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)


class InputScreen(Screen):
    BINDINGS = [
        Binding("ctrl+enter", "submit", "开始搜索"),
        Binding("/", "focus_idea", "聚焦输入"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._idea = ""
        self._language_filter = ""
        self._slash_items: list[tuple[str, object]] = []
        self._slash_visible = False

    def compose(self) -> ComposeResult:
        with Vertical(id="input-scroll"):
            yield Static("🧭 Idea Search — 想法搜索", id="hero-title")
            yield Static(
                "用一句话描述你的想法，我来帮你找 GitHub 上有没有现成的项目",
                id="hero-sub",
            )
            with Center():
                with Vertical(id="input-card"):
                    yield Static("你的想法", classes="field-label")
                    yield SlashTextArea(
                        self._idea,
                        id="idea-input",
                        placeholder="例如：自托管的个人知识库 + AI 问答",
                    )
                    yield Static("语言过滤（可选，如 python）", classes="field-label")
                    yield Input(
                        self._language_filter,
                        id="lang-input",
                        placeholder="留空则不过滤",
                    )
                    yield Button("🔍 开始搜索", id="submit-btn", variant="primary")
                    slash_options = OptionList(id="slash-options")
                    slash_options.styles.display = "none"
                    yield slash_options
            yield Static("提示：ctrl+enter 或点击按钮开始搜索，或输入 / 查看命令", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self.apply_values)
        self.call_after_refresh(self.action_focus_idea)

    def set_values(self, idea: str, language_filter: str | None = None) -> None:
        self._idea = idea
        self._language_filter = language_filter or ""
        self.apply_values()

    def apply_values(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#idea-input", TextArea).text = self._idea
        self.query_one("#lang-input", Input).value = self._language_filter

    def _gather(self) -> tuple[str, str | None]:
        idea = self.query_one("#idea-input", TextArea).text.strip()
        lang = self.query_one("#lang-input", Input).value.strip() or None
        return idea, lang

    def action_submit(self) -> None:
        idea, lang = self._gather()
        if not idea:
            return
        if idea.startswith("/"):
            token = idea.split()[0]
            if token in _SLASH_COMMANDS:
                self._run_slash(token)
            else:
                self.app.notify(f"未知命令: {token}")  # type: ignore[attr-defined]
            return
        self.app.start_search(idea, lang)  # type: ignore[attr-defined]

    def action_focus_idea(self) -> None:
        self.query_one("#idea-input", TextArea).focus()

    def _run_slash(self, name: str) -> None:
        app = self.app  # type: ignore[attr-defined]
        self._idea = ""
        self.apply_values()
        self._hide_slash()
        if name == "/history":
            app.push_screen(HistoryScreen())
        elif name == "/clear-history":
            app._history_store.clear()
            app.notify("历史已清空")
            self.query_one("#idea-input", TextArea).focus()
        elif name == "/help":
            app.notify("/history /clear-history /help /quit")
        elif name == "/quit":
            app.exit()

    def _refresh_slash(self, needle: str) -> None:
        if not self.is_mounted:
            return
        items: list[tuple[str, object]] = []
        prompts: list[Option] = []
        for name in _SLASH_COMMANDS:
            if name.startswith("/" + needle):
                items.append(("cmd", name))
                prompts.append(Option(name))
        options = self.query_one("#slash-options", OptionList)
        options.clear_options()
        if not prompts:
            self._hide_slash()
            return
        self._slash_items = items
        for prompt in prompts:
            options.add_option(prompt)
        options.highlighted = 0
        options.styles.display = "block"
        self._slash_visible = True

    def _hide_slash(self) -> None:
        self._slash_visible = False
        self._slash_items = []
        if not self.is_mounted:
            return
        self.query_one("#slash-options", OptionList).styles.display = "none"

    def handle_slash_key(self, key: str) -> bool:
        if not self._slash_visible or not self.is_mounted:
            return False
        options = self.query_one("#slash-options", OptionList)
        if key == "up":
            options.action_cursor_up()
            return True
        if key == "down":
            options.action_cursor_down()
            return True
        if key in ("enter", "tab"):
            self._activate_slash()
            return True
        if key == "escape":
            self._hide_slash()
            return True
        return False

    def _activate_slash(self) -> None:
        if not self._slash_visible or not self.is_mounted:
            return
        options = self.query_one("#slash-options", OptionList)
        index = options.highlighted
        if index is None or not (0 <= index < len(self._slash_items)):
            return
        kind, payload = self._slash_items[index]
        if kind == "cmd":
            self._run_slash(str(payload))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "idea-input":
            return
        text = event.text_area.text
        if text.startswith("/"):
            self._refresh_slash(text[1:])
        else:
            self._hide_slash()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option_list.id == "slash-options":
            self._activate_slash()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lang-input":
            self.action_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-btn":
            self.action_submit()


class LoadingScreen(Screen):
    def __init__(self) -> None:
        super().__init__()
        self._lines: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-card"):
            yield LoadingIndicator()
            yield Static("正在搜索，请稍候…", id="loading-title")
            yield RichLog(id="progress-log", markup=True, highlight=False)
        yield Footer()

    def reset(self) -> None:
        self._lines = []
        if self.is_mounted:
            self.query_one("#progress-log", RichLog).clear()

    def add_progress(self, message: str) -> None:
        self._lines.append(message)
        if self.is_mounted:
            self.query_one("#progress-log", RichLog).write(message)


class ResultsScreen(Screen):
    BINDINGS = [
        Binding("o", "open_selected", "打开仓库"),
        Binding("enter", "open_selected", "打开仓库"),
        Binding("s", "resubmit", "重新搜索"),
        Binding("escape", "back", "返回"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._result: SearchResult | None = None
        self._repos_by_key: dict[str, Repo] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="results-layout"):
            yield Static("", id="results-title")
            with Horizontal(id="results-body"):
                yield DataTable(id="repo-table", cursor_type="row", zebra_stripes=True)
                yield Static("", id="detail", markup=True)
            yield Static("", id="summary", markup=True)
        yield Footer()

    def set_result(self, result: SearchResult) -> None:
        self._result = result
        self._populate()

    def on_mount(self) -> None:
        self.call_after_refresh(self._populate)

    def _populate(self) -> None:
        if self._result is None or not self.is_mounted:
            return
        result = self._result
        self.query_one("#results-title", Static).update(f"[bold]想法: [/]{result.idea}")

        summary: Static = self.query_one("#summary", Static)
        summary.styles.display = "block" if result.summary else "none"
        if result.summary:
            summary.update(f"[bold]总结[/]\n{result.summary}")

        table = self.query_one("#repo-table", DataTable)
        table.clear(columns=True)
        table.add_column("排名", width=6)
        table.add_column("⭐", width=10)
        table.add_column("评分", width=6)
        table.add_column("仓库", width=None, key="repo")
        table.add_column("匹配理由", width=30)
        self._repos_by_key = {}
        for rank, repo in enumerate(result.repositories, 1):
            reason = repo.reason
            if len(reason) > 28:
                reason = reason[:28] + "…"
            row_key = table.add_row(
                str(rank),
                f"⭐{repo.stars:,}",
                _score_badge(repo.score),
                repo.full_name,
                reason,
                key=repo.full_name,
            )
            self._repos_by_key[row_key.value] = repo
        if result.repositories:
            table.move_cursor(row=0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        repo = self._repos_by_key.get(event.row_key.value)
        if repo is not None:
            self.query_one("#detail", Static).update(_repo_detail_markup(repo))

    def _selected_repo(self) -> Repo | None:
        table = self.query_one("#repo-table", DataTable)
        coords = table.cursor_coordinate
        row_key = table.coordinate_to_cell_key(coords).row_key
        return self._repos_by_key.get(row_key.value)

    def action_open_selected(self) -> None:
        repo = self._selected_repo()
        if repo is not None:
            self.app.open_repo(repo.url)  # type: ignore[attr-defined]

    def action_resubmit(self) -> None:
        app = self.app  # type: ignore[attr-defined]
        app.switch_screen("input")
        if self._result is not None:
            app._input_screen.set_values(self._result.idea)
        app._input_screen.call_after_refresh(app._input_screen.action_focus_idea)

    def action_back(self) -> None:
        app = self.app  # type: ignore[attr-defined]
        app.switch_screen("input")
        app._input_screen.call_after_refresh(app._input_screen.action_focus_idea)


class ErrorScreen(Screen):
    BINDINGS = [Binding("escape", "go_back", "返回"), Binding("q", "quit", "退出")]

    def __init__(self) -> None:
        super().__init__()
        self._message = ""
        self._hint = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="error-card"):
            yield Static("⚠️ 出错了", id="error-title")
            yield Static(self._message, id="error-message", markup=True)
            yield Static(self._hint, id="error-hint", markup=True)
            yield Button("返回重新输入", id="error-back")
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self.apply_values)

    def set_error(self, message: str, hint: str) -> None:
        self._message = message
        self._hint = hint
        self.apply_values()

    def apply_values(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#error-message", Static).update(self._message)
        self.query_one("#error-hint", Static).update(self._hint)

    def action_go_back(self) -> None:
        app = self.app  # type: ignore[attr-defined]
        app.switch_screen("input")
        app._input_screen.call_after_refresh(app._input_screen.action_focus_idea)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "error-back":
            self.action_go_back()


class HistoryScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", "关闭"),
        Binding("d", "delete_selected", "删除"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._option_entries: list[HistoryEntry | None] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="history-card"):
            yield Static("历史搜索", id="history-title")
            yield Input(
                "",
                id="history-filter",
                placeholder="输入关键词过滤，回车查看，d 删除，Esc 关闭",
            )
            yield OptionList(id="history-options")
            yield Static(
                "Tab 切换过滤/列表 · ↑↓ 选择 · 回车查看 · d 删除 · Esc 关闭",
                id="history-hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self._initial_refresh)

    def _initial_refresh(self) -> None:
        if not self.is_mounted:
            return
        needle = self.query_one("#history-filter", Input).value
        self._rebuild(needle)
        self._focus_filter()

    def _focus_filter(self) -> None:
        if self.is_mounted:
            self.query_one("#history-filter", Input).focus()

    def _row_label(self, entry: HistoryEntry) -> str:
        timestamp = entry.timestamp[5:16].replace("T", " ")
        idea = entry.result.idea
        if len(idea) > 40:
            idea = idea[:37] + "…"
        return f"{timestamp}  {idea}  [{len(entry.result.repositories)}个结果]"

    def _rebuild(self, needle: str) -> None:
        if not self.is_mounted:
            return
        store = self.app._history_store  # type: ignore[attr-defined]
        entries = [e for e in store.load() if needle in e.result.idea]
        options = self.query_one("#history-options", OptionList)
        options.clear_options()
        self._option_entries = []
        if not entries:
            options.add_option(Option("暂无历史搜索记录", disabled=True))
            self._option_entries = [None]
        else:
            for entry in entries:
                options.add_option(Option(self._row_label(entry)))
                self._option_entries.append(entry)
        options.highlighted = 0

    def _selected_entry(self) -> HistoryEntry | None:
        if not self.is_mounted:
            return None
        options = self.query_one("#history-options", OptionList)
        index = options.highlighted
        if index is None or not (0 <= index < len(self._option_entries)):
            return None
        return self._option_entries[index]

    def _view_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        app = self.app  # type: ignore[attr-defined]
        app.pop_screen()
        app._results_screen.set_result(entry.result)
        app.switch_screen("results")

    def action_close(self) -> None:
        self.app.pop_screen()  # type: ignore[attr-defined]

    def action_delete_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        app = self.app  # type: ignore[attr-defined]
        app._history_store.delete(entry.id)
        app.notify("已删除该条历史")
        needle = self.query_one("#history-filter", Input).value
        self._rebuild(needle)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "history-filter":
            self._rebuild(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "history-filter":
            self.query_one("#history-options", OptionList).focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option_list.id == "history-options":
            self._view_selected()


class IdeaSearchApp(App):
    TITLE = "Idea Search"
    SUB_TITLE = "想法搜索"

    CSS = """
    $accent: #7aa2f7;
    $background: #16161e;
    $panel: #1f2335;
    $surface: #24283b;
    $text: #c0caf5;
    $text-muted: #565f89;
    $error: #f7768e;

    Screen { background: $background; color: $text; }
    Header { background: $surface; }
    Footer { background: $surface; }
    Button.primary { background: $accent; color: #16161e; }

    #input-scroll { height: 1fr; padding-top: 4; }
    #hero-title { content-align: center middle; text-style: bold; color: $accent; height: 3; }
    #hero-sub { content-align: center middle; color: $text-muted; height: 2; margin-bottom: 1; }
    #input-card { width: 72; max-width: 92%; border: round $accent; background: $panel; padding: 1 2; }
    .field-label { margin-top: 1; color: $text-muted; }
    #idea-input { height: 5; margin: 1 0; }
    #lang-input { margin: 1 0; }
    #submit-btn { margin-top: 1; width: 100%; }
    #slash-options { height: auto; max-height: 8; margin-top: 1; }
    #hint { content-align: center middle; color: $text-muted; height: 2; margin-top: 1; }

    HistoryScreen { align: center middle; }
    #history-card { width: 76; max-width: 94%; height: auto; max-height: 90%; border: round $accent; background: $panel; padding: 1 2; }
    #history-title { content-align: center middle; text-style: bold; color: $accent; height: 2; }
    #history-filter { margin: 1 0; }
    #history-options { height: 1fr; max-height: 16; }
    #history-hint { content-align: center middle; color: $text-muted; height: 2; margin-top: 1; }

    #loading-card { width: 72; max-width: 92%; border: round $accent; background: $panel; padding: 1 2; }
    #loading-title { content-align: center middle; color: $accent; text-style: bold; height: 3; }
    #progress-log { height: 14; }

    #results-layout { height: 1fr; padding: 0 1; }
    #results-title { height: 3; content-align: left middle; text-style: bold; color: $accent; }
    #results-body { height: 1fr; }
    #repo-table { width: 2fr; border: round $accent; background: $panel; }
    DataTable > .datatable--cursor { background: $accent 25%; }
    DataTable > .datatable--header { background: $surface; color: $accent; text-style: bold; }
    #detail { width: 3fr; border: round $accent; background: $panel; padding: 1 2; }
    #summary { height: auto; border: round $accent; background: $panel; padding: 1 2; margin-top: 1; }

    #error-card { width: 76; max-width: 92%; border: round $error; background: $panel; padding: 1 2; }
    #error-title { content-align: center middle; color: $error; text-style: bold; height: 3; }
    #error-message { color: $error; }
    #error-hint { color: $text-muted; margin-top: 1; }
    #error-back { margin-top: 2; width: 100%; background: $accent; color: #16161e; }
    """

    BINDINGS = [Binding("q", "quit", "退出")]

    def __init__(
        self,
        idea: str | None = None,
        service: object | None = None,
        language_filter: str | None = None,
    ) -> None:
        super().__init__()
        self._idea = idea
        self._service = service
        self._language_filter = language_filter
        self._history_store = HistoryStore()
        self._input_screen = InputScreen()
        self._loading_screen = LoadingScreen()
        self._results_screen = ResultsScreen()
        self._error_screen = ErrorScreen()

    async def on_mount(self) -> None:
        self.install_screen(self._input_screen, "input")
        self.install_screen(self._loading_screen, "loading")
        self.install_screen(self._results_screen, "results")
        self.install_screen(self._error_screen, "error")
        await self.push_screen("input")
        if self._idea:
            self.start_search(self._idea, self._language_filter)

    def _get_service(self) -> object:
        if self._service is None:
            from idea_search.search import SearchService

            self._service = SearchService()
        return self._service

    def start_search(self, idea: str, language_filter: str | None = None) -> None:
        self._idea = idea
        self._language_filter = language_filter
        self.run_worker(
            self._run_search(idea, language_filter),
            name="search",
            exclusive=True,
            group="search",
        )

    def _on_progress(self, message: str) -> None:
        self._loading_screen.add_progress(message)

    async def _run_search(
        self, idea: str, language_filter: str | None
    ) -> None:
        await self.switch_screen("loading")
        self._loading_screen.reset()
        service = self._get_service()
        try:
            result = await service.search(
                idea,
                language_filter=language_filter,
                on_progress=self._on_progress,
            )
        except Exception as exc:
            message, hint = self._format_error(exc)
            self._error_screen.set_error(message, hint)
            await self.switch_screen("error")
            return
        self._results_screen.set_result(result)
        try:
            self._history_store.add(result)
        except Exception as exc:
            self.log.error("failed to persist search history:", exc)
        await self.switch_screen("results")

    def _format_error(self, exc: Exception) -> tuple[str, str]:
        try:
            from idea_search.config import ConfigError

            is_config_error = isinstance(exc, ConfigError)
        except Exception:
            is_config_error = False
        if is_config_error:
            return (
                str(exc),
                "配置提示：请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY，"
                "或在项目根目录创建 .env 文件后重试。",
            )
        return (
            f"搜索失败：{exc}",
            "请稍后重试（如连续失败，检查网络连接与 API 配置）。",
        )

    @work(thread=True)
    def open_repo(self, url: str) -> None:
        webbrowser.open(url)


def run_tui(idea: str | None = None, language_filter: str | None = None) -> None:
    IdeaSearchApp(idea=idea, language_filter=language_filter).run()