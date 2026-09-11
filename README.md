# AgentMonitor V3 — Pi 运行状态监控看板

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![tests](https://github.com/18611198255/AgentMonitor/actions/workflows/test.yml/badge.svg)](https://github.com/18611198255/AgentMonitor/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#技术)

一个**零侵入、只读**的本地看板，实时监控你跑的 **Pi 会话**（网页版 + CLI 版），
聚合 Token/成本，异常告警，支持历史回看 + 搜索与会话管理。

- **CLI 版**：扫描本机 `pi` 进程（`pgrep -x pi` + `lsof` 拿 cwd/tty），匹配落盘的 jsonl 会话。
- **网页版**：轮询 `http://127.0.0.1:30141`（pi-web）的 `/api/agent/running`，识别浏览器里跑的会话。

## 快速开始

```bash
cd AgentMonitor          # 换成你 clone 下来的实际目录
./start.sh               # 起 watchdog 守护 + 本地服务，并打开浏览器
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

停止（**务必先杀 watchdog，再杀 server**——若先杀 server，watchdog 会在 10 秒内自动把它重新拉起，导致永远停不掉）：

```bash
kill $(cat /tmp/agentmonitor_watchdog_v3_8571.pid)   # 1) 先杀守护，防止它 10s 内拉起 server
pkill -f 'agentmonitor.server'                       # 2) 再杀服务本身
```

## 技术

- **Python 版本**：支持 **3.9+**（CI 矩阵实测 3.9 / 3.10 / 3.11 / 3.12 / 3.13 全通过）。
- 零第三方依赖：Python 标准库（`http.server` / `sqlite3` / `subprocess` / `urllib` / `json` / `unittest`），前端原生 HTML/JS/CSS（无框架无 CDN）。
- 模块化包：`parser`（jsonl→Session 唯一解析）、`detectors/`（CLI + 网页版探测器统一 `detect()` 接口）、`merge`（合并去重）、`index`（SQLite 索引）、`aggregator`（聚合）、`alerts`（告警）、`actions`（kill/restart）、`server`（HTTP 路由，唯一入口）。
- 安全：`/api/jump` 路径白名单（仅 `~/.pi/agent/sessions` 内）、`/api/kill` 进程白名单、`HEAD` 统一 404、跳转脚本仅拼接 iTerm 自身返回的 `unique id`（防注入/防泄露源码）。

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AGENTMONITOR_PORT` | `8571` | 服务端口（watchdog 与 start.sh 共用） |
| `AGENTMONITOR_PYTHON` | 自动探测 | 指定 Python 解释器（watchdog 拉起服务用） |

## 常见问题

**Q: 在 Python 3.9 上报 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`？**

这曾经是个真实 bug（已修复，见 commit `75749e2`）。原因是代码里用了 `float | None` 这种 PEP 604 联合类型写法，**仅 Python 3.10+ 支持**。现已全部改为 `Optional[...]`，3.9 起可用。若你从旧版本升级，请拉取最新 `main`。

**Q: 本地测试全过，CI 却挂在 3.9？**

因为本地 Python 版本较高（如 3.13），掩盖了低版本语法/API 兼容问题。CI 跑 3.9–3.13 矩阵就是为了提前暴露这类问题。

**Q: 端口被占用 / 想换端口？**

```bash
AGENTMONITOR_PORT=9000 ./start.sh
```

## License

[MIT](LICENSE) © 2026 18611198255
