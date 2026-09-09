# CHANGELOG

本项目更改记录。每条按「日期 — 主题」列出：改了什么 / 为什么 / 怎么撤销。

---

## 2026-09-09 — V3 首版：模块化重构 + 4 项新功能 + 浅色仪表盘 + 守护交付

### 背景（为什么做）
V2 是单文件 `monitor_server.py`（约 45KB），所有逻辑（进程扫描、网页版轮询、jsonl 解析、
状态推断、跳转、路由）堆在一个文件里，难维护、难测试。V3 目标是把它重构为**模块化包**
（零第三方依赖），并补上 V2 没有的 4 项能力：成本/Token 聚合、异常告警、历史回看+搜索、
会话管理；同时把看板换成浅色仪表盘风，加 watchdog 守护自愈。

### V3 相对 V2 的变化

**1. 模块化包（V2 单文件 → 8 个模块）**

| V2 | V3 |
|---|---|
| `monitor_server.py`（单文件，全部逻辑） | `agentmonitor/` 包：`parser` / `detectors/{cli,web}` / `merge` / `index` / `aggregator` / `alerts` / `actions` / `server` |
| `dashboard.html` 与代码同目录 | `dashboard.html` 移到项目根，`server.py` 静态服务用项目根定位 |
| `python3 monitor_server.py` | `python3 -m agentmonitor.server`（唯一入口） |

- `parser.py`：唯一 jsonl→Session 状态解析（V2 的 `parse_session_jsonl` + 状态推断收敛为纯函数）。
- `detectors/`：统一 `Detector.detect() -> [SessionRef]` 接口；`cli.py`（`pgrep -x pi`+`lsof`）、`web.py`（pi-web `/api/agent/running`）。
- `merge.py`：两个探测器结果合并去重。
- `index.py`：SQLite 派生索引（sessions / daily_usage），jsonl 仍是唯一事实源。

**2. 4 项新功能（F2-F5）**

- **成本/Token 聚合**（`aggregator.py` + `/api/stats`）：今日 / 本周 / 总计 / 按项目 / 近 7 天趋势。
- **异常告警**（`alerts.py` + `/api/alerts` + 后台 30s 扫描线程）：stuck / silent / piweb_down 三类规则，macOS 通知 + 冷却去重。
- **历史回看 + 搜索**（`index.py` + `/api/sessions?q=&project=&all=1`）：搜历史会话。
- **会话管理**（`actions.py` + `/api/kill` / `/api/restart`）：终止 / 重启 CLI 会话。

**3. 浅色仪表盘**（`dashboard.html`）
浅底 + 状态色块（运行绿 / 等输入橙 / 卡住红 / 空闲灰 / 新对话蓝）+ 数据卡片网格 + 顶部 hero 统计，
替换 V2 的白底卡片风。

**4. 安全修复（沿用 V2，V3 保留并收敛）**

- **HEAD 白名单**：`do_HEAD` 不继承 `SimpleHTTPRequestHandler.do_HEAD`（父类 `send_head→translate_path`
  会把路径解析到 cwd 并返回 200+Content-Length，泄露项目根源码/.git 的存在与大小），统一返回 404。
- **applescript 注入面收敛**：`/api/jump?file=` 先经 `_path_within_sessions` 白名单校验（仅 `~/.pi/agent/sessions`
  内文件），且跳转脚本只拼接 iTerm 自身返回的 `the unique id`（非用户输入），堵住注入与任意文件读取。
- 路由白名单：除看板页与 `/api/*` 外一律 404。

**5. watchdog 守护 + 启动脚本 + 文档**

- `watchdog.py`：自 V2 `agentmonitor_watchdog.py` 移植，入口改为 `python3 -m agentmonitor.server`（`cwd=PROJECT_ROOT`），
  pid/日志文件加 `_v3` 后缀避免与 V2 的 `/tmp` 文件冲突。
- `start.sh`：起 watchdog + 打开浏览器。
- `README.md`：用法、API 端点表、数据源、watchdog 说明。
- 本 `CHANGELOG.md`。

### 文件结构

```
AgentMonitor-v3/
├── agentmonitor/
│   ├── __init__.py            # 版本 3.0.0 + 常量导出
│   ├── constants.py           # 路径/端口/阈值/窗口常量（从 V2 收敛）
│   ├── parser.py              # jsonl → Session（唯一状态解析）
│   ├── detectors/{base,cli,web}.py   # 统一 Detector 接口
│   ├── merge.py               # 合并去重
│   ├── index.py               # SQLite 索引（sessions / daily_usage）
│   ├── aggregator.py          # 成本/Token 聚合
│   ├── alerts.py              # 异常规则 + 通知
│   ├── actions.py             # kill / restart
│   └── server.py              # HTTP 服务 + 路由（唯一入口）
├── tests/                     # unittest（零第三方依赖）
├── dashboard.html             # 浅色仪表盘看板
├── watchdog.py                # 守护自愈
├── start.sh
├── README.md
├── CHANGELOG.md
└── docs/（PRD + 开发规划）
```

### 验证

- `python3 -m py_compile watchdog.py` — 语法正确。
- `python3 -c "import agentmonitor.server"` — 包可导入。
- `bash -n start.sh` — 脚本语法正确。
- 单测：`python3 -m unittest discover -s tests`（parser/index/aggregator/alerts/cli/web/merge/actions）。
- watchdog 自愈实跑验证：留待 Task 13（需先协调停 V2，避免 8571 端口冲突）。
