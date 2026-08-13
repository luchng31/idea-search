"""Textual TUI for idea-search.

Flow: input screen -> loading (live progress) -> results (list + detail),
with an error screen for configuration/search failures.
"""

from __future__ import annotations

import webbrowser

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    RichLog,
    Static,
    TextArea,
)
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


class InputScreen(Screen):
    BINDINGS = [
        Binding("ctrl+enter", "submit", "开始搜索"),
        Binding("/", "focus_idea", "聚焦输入"),
        Binding("h", "focus_history", "历史"),
        Binding("v", "view_history", "回看"),
        Binding("d", "delete_history", "删除"),
        Binding("c", "clear_history", "清空"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._idea = ""
        self._language_filter = ""
        self._history_entries: list[HistoryEntry] = []
        self._idea_by_item: dict[ListItem, str] = {}
        self._entry_by_item: dict[ListItem, HistoryEntry] = {}

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
                    yield TextArea(
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
            yield Static("提示：ctrl+enter 或点击按钮开始搜索", id="hint")
            with Center():
                yield Static("历史搜索", id="history-label", classes="field-label")
            with Center():
                history_list = ListView(id="history-list")
                history_list.styles.display = "none"
                yield history_list
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self.apply_values)
        self.call_after_refresh(self._apply_history)

    def set_values(self, idea: str, language_filter: str | None = None) -> None:
        self._idea = idea
        self._language_filter = language_filter or ""
        self.apply_values()

    def apply_values(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#idea-input", TextArea).text = self._idea
        self.query_one("#lang-input", Input).value = self._language_filter

    def set_history(self, entries: list[HistoryEntry]) -> None:
        self._history_entries = list(entries)
        self._apply_history()

    def _apply_history(self) -> None:
        if not self.is_mounted:
            return
        list_view = self.query_one("#history-list", ListView)
        list_view.clear()
        self._idea_by_item = {}
        self._entry_by_item = {}
        for entry in self._history_entries:
            item = ListItem(Label(self._history_label(entry)))
            self._idea_by_item[item] = entry.result.idea
            self._entry_by_item[item] = entry
            list_view.append(item)
        visible = "block" if self._history_entries else "none"
        list_view.styles.display = visible
        self.query_one("#history-label", Static).styles.display = visible

    def _history_label(self, entry: HistoryEntry) -> str:
        timestamp = entry.timestamp[5:16].replace("T", " ")
        idea = entry.result.idea
        if len(idea) > 60:
            idea = idea[:57] + "…"
        return f"{timestamp}  {idea}"

    def _gather(self) -> tuple[str, str | None]:
        idea = self.query_one("#idea-input", TextArea).text.strip()
        lang = self.query_one("#lang-input", Input).value.strip() or None
        return idea, lang

    def action_submit(self) -> None:
        idea, lang = self._gather()
        if idea:
            self.app.start_search(idea, lang)  # type: ignore[attr-defined]

    def action_focus_idea(self) -> None:
        self.query_one("#idea-input", TextArea).focus()

    def action_focus_history(self) -> None:
        if self._history_entries:
            list_view = self.query_one("#history-list", ListView)
            list_view.index = 0
            list_view.focus()

    def _selected_entry(self) -> HistoryEntry | None:
        list_view = self.query_one("#history-list", ListView)
        item = list_view.highlighted_child
        if item is None:
            return None
        return self._entry_by_item.get(item)

    def action_view_history(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self.app._results_screen.set_result(entry.result)  # type: ignore[attr-defined]
        self.app.switch_screen("results")  # type: ignore[attr-defined]

    def action_delete_history(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self.app._history_store.delete(entry.id)  # type: ignore[attr-defined]
        self.app._refresh_history_input()  # type: ignore[attr-defined]
        if self._history_entries:
            list_view = self.query_one("#history-list", ListView)
            list_view.index = 0
            list_view.focus()
        else:
            self.query_one("#idea-input", TextArea).focus()

    def action_clear_history(self) -> None:
        self.app._history_store.clear()  # type: ignore[attr-defined]
        self.app._refresh_history_input()  # type: ignore[attr-defined]
        self.query_one("#idea-input", TextArea).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idea = self._idea_by_item.get(event.item)
        if idea is None:
            return
        self._idea = idea
        self.apply_values()
        self.query_one("#idea-input", TextArea).focus()

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

    def action_back(self) -> None:
        self.app.switch_screen("input")  # type: ignore[attr-defined]


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
        self.app.switch_screen("input")  # type: ignore[attr-defined]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "error-back":
            self.action_go_back()


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
    #history-label { content-align: center middle; }
    #idea-input { height: 5; margin: 1 0; }
    #lang-input { margin: 1 0; }
    #submit-btn { margin-top: 1; width: 100%; }
    #hint { content-align: center middle; color: $text-muted; height: 2; margin-top: 1; }
    #history-list { width: 72; max-width: 92%; height: auto; max-height: 10; margin-top: 1; border: round $accent; background: $panel; }

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
        self._input_screen.set_history(self._history_store.load())
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

    def _refresh_history_input(self) -> None:
        self._input_screen.set_history(self._history_store.load())

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
            self._refresh_history_input()
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