# idea-search

用自然语言描述一个想法,在 GitHub 上发现已有的匹配项目。LLM 负责把想法翻译成搜索策略并评审匹配度,无需手工构造搜索关键词。

## 功能

- 自然语言描述想法 → LLM 生成关键词、查询与语言过滤
- 在 GitHub 仓库搜索(描述/名称/主题),按星标与匹配度去重排序
- LLM 逐个评审项目与想法的匹配度(0-100),并输出整体总结
- 双界面:终端 TUI(交互式)与 `--text` 纯文本模式(脚本友好)

## 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

需要 Python >= 3.11。依赖: `textual`、`httpx`、`openai`、`python-dotenv`。

## 配置

```bash
cp .env.example .env
```

编辑 `.env`:

| 变量 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 二选一 | 首选。DeepSeek 密钥 |
| `OPENAI_API_KEY` | 二选一 | 备用。任何 OpenAI 协议兼容服务的密钥 |
| `LLM_BASE_URL` | 否 | 服务地址,默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 否 | 模型名,默认 `deepseek-chat` |
| `GITHUB_TOKEN` | 否 | 不填也能搜,但未认证限流更严格(10 次/分钟) |

配置文件查找顺序:当前目录 `.env` → 用户主目录 `~/.env`。

## 用法

### 文本模式

```bash
idea-search "自托管的个人知识库 + AI 问答" --text
idea-search "终端里的 AI 助手" --text --lang python
```

输出搜索策略、匹配项目(带星标与匹配分)和整体总结;进度消息走 stderr,便于管道处理。

### TUI 模式

```bash
idea-search "想法描述"
idea-search                              # 启动后自行输入
```

| 界面 | 按键 | 作用 |
|---|---|---|
| 输入 | `Ctrl+Enter` | 开始搜索 |
| 输入 | `/` | 聚焦输入框(启动/返回输入屏时已自动聚焦,直接打字即可) |
| 输入 | `/` 开头打字 | 弹出命令下拉(4 个命令按前缀过滤) |
| 输入·下拉 | `↑`/`↓` | 移动高亮 |
| 输入·下拉 | `Enter` | 执行高亮的命令 |
| 输入·下拉 | `Esc` | 关闭下拉,保留已输入文本 |
| 输入 | `/history` + 回车 | 打开历史浏览界面(见下) |
| 输入 | `/clear-history` + 回车 | 清空全部历史 |
| 输入 | `/help` + 回车 | 显示命令一览 |
| 输入 | `/quit` + 回车 | 退出 |
| 结果 | `o` / `Enter` | 用默认浏览器打开选中仓库 |
| 结果 | `Esc` | 返回输入屏(结果保留) |
| 结果 | `s` | 把当前想法填入输入框,可修改后再搜(复用) |
| 任意 | `q` | 退出 |
| 错误 | `Escape` | 返回输入界面 |

### 历史浏览(`/history`)

打开一个居中 overlay,按时间倒序列出每次搜索(时间 + 想法 + 结果数),用法对标 `/sessions` 式会话选择器:

| 按键 | 作用 |
|---|---|
| 打字 | 按想法关键词实时过滤 |
| `Tab` | 在过滤框与列表间切换焦点 |
| `↑`/`↓` | 选择条目 |
| `Enter` | 离线查看该条上次的搜索结果(不重新搜索) |
| `d` | 删除高亮条目 |
| `Esc` | 关闭 overlay,返回输入屏 |

查看历史结果后,在结果屏按 `s` 可把想法填入输入框修改重搜;空历史显示"暂无历史搜索记录"。

### 开发

```bash
pip install -e ".[dev]"
pytest
```

测试全部使用 fake,不访问网络或 LLM。

## 项目结构

```
src/idea_search/
├── config.py    # 环境配置加载与校验
├── llm.py       # LLM 客户端:生成策略、评审项目
├── github.py    # GitHub 搜索客户端
├── search.py    # 编排管线:计划 → 搜索 → 评审 → 排序
├── models.py    # 数据模型与排序逻辑
├── cli.py       # 命令行入口(--text / TUI 选择)
└── tui.py       # Textual 终端界面
```

## 免责声明

所有输出由 AI 生成,项目匹配度仅供参考。请自行核实仓库的许可证、维护状态与代码质量。
