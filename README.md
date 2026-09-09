# AgentMonitor V3 — Pi 运行状态监控看板

一个**零侵入、只读**的本地看板，实时监控你跑的 **Pi 会话**（网页版 + CLI 版），
聚合 Token/成本，异常告警，支持历史回看 + 搜索与会话管理。

- **CLI 版**：扫描本机 `pi` 进程（`pgrep -x pi` + `lsof` 拿 cwd/tty），匹配落盘的 jsonl 会话。
- **网页版**：轮询 `http://127.0.0.1:30141`（pi-web）的 `/api/agent/running`，识别浏览器里跑的会话。

## 快速开始

```bash
cd "/Users/kelvinjiang/Desktop/实用工具开发/AgentMonitor-v3"
./start.sh            # 起 watchdog 守护 + 本地服务，并打开浏览器
```

或直接前台起服务（不守护）：

```bash
python3 -m agentmonitor.server            # 默认端口 8571
python3 -m agentmonitor.server 9000       # 指定端口
```

看板地址：`http://127.0.0.1:8571/`（服务常驻后台时，直接刷新浏览器即可）。

## 功能

- **实时监控**：按项目名分组展示会话卡片（状态徽章、最近活动、当前工具、模型、Token/花费），每 3 秒自动刷新。
- **成本/Token 聚合**：今日 / 本周 / 总计 / 按项目 / 近 7 天趋势（`/api/stats`）。
- **异常告警**：卡住（stuck）/ 静默（silent）/ pi-web 掉线三类规则，macOS 通知 + 冷却去重（`/api/alerts`）。
- **历史回看 + 搜索**：`?q=` / `?project=` / `?all=1` 走 SQLite 索引，搜历史会话。
- **会话管理**：`/api/kill`（终止 CLI 会话）、`/api/restart`（在原 cwd 重启），破坏性操作带白名单校验。
- **「去终端」跳转**：通过 `osascript` 定位对应 iTerm2 标签页并 select。

## API 端点

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/` | 看板页面（dashboard.html） |
| GET | `/api/sessions` | 活跃会话列表；`?q=`/`?project=`/`?all=1` 走历史索引搜索 |
| GET | `/api/stats` | 成本/Token 聚合（today/week/total/by_project/trend_7d） |
| GET | `/api/alerts` | 最近告警列表（最多 50 条） |
| GET | `/api/services` | 服务健康检查（dashboard/pi-web 状态） |
| GET | `/api/pi-procs` | 当前真实在跑的 pi CLI 进程（pid + tty + cwd，绕过缓存） |
| GET | `/api/jump?file=<path>` | 跳转到该 jsonl 对应的 iTerm2 标签页（白名单校验） |
| POST | `/api/kill` | `{"pid": "..."}` 终止指定 CLI 会话（白名单校验） |
| POST | `/api/restart` | `{"pid": "...", "cwd": "..."}` 终止后在原 cwd 重启 pi 会话（白名单校验） |

所有 API 响应均 `Cache-Control: no-store`；除看板页与 `/api/*` 外一律 404。

## 数据源

- 会话数据：`~/.pi/agent/sessions/**/*.jsonl`（**只读**，不写回、不修改 Pi 任何配置）。
- 派生索引：`~/.pi/agent/agentmonitor_v3.sqlite`（历史/搜索用，jsonl 仍是唯一事实源）。
- pi-web 地址：`http://127.0.0.1:30141`。

## watchdog 守护自愈

`watchdog.py` 是 macOS 守护进程（双 fork + setsid），每 10 秒健康检查 8571 端口 + `/api/services`，
服务死了自动 `setsid` 拉起 `python3 -m agentmonitor.server`。

- pid 文件：`/tmp/agentmonitor_watchdog_v3.pid`
- 守护日志：`/tmp/agentmonitor_watchdog_v3.log`
- 服务日志：`/tmp/agentmonitor_server_v3.log`

停止：`pkill -f 'agentmonitor.server'`（服务）、`kill $(cat /tmp/agentmonitor_watchdog_v3.pid)`（守护）。

## 技术

- 零第三方依赖：Python 标准库（`http.server` / `sqlite3` / `subprocess` / `urllib` / `json` / `unittest`），前端原生 HTML/JS/CSS（无框架无 CDN）。
- 模块化包：`parser`（jsonl→Session 唯一解析）、`detectors/`（CLI + 网页版探测器统一 `detect()` 接口）、`merge`（合并去重）、`index`（SQLite 索引）、`aggregator`（聚合）、`alerts`（告警）、`actions`（kill/restart）、`server`（HTTP 路由，唯一入口）。
- 安全：`/api/jump` 路径白名单（仅 `~/.pi/agent/sessions` 内）、`/api/kill` 进程白名单、`HEAD` 统一 404、跳转脚本仅拼接 iTerm 自身返回的 `unique id`（防注入/防泄露源码）。
