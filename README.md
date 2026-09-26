# 海上搜救协调系统

标准库实现的独立协调原型，使用 SQLite 保存事件、搜救资源、搜索区域、线索、离线批次和时间线。

## 运行

要求 Python 3.11+（在当前 Python 3.9 环境也可运行）。

```bash
python3 app.py --init --seed
python3 app.py
```

默认地址为 `http://127.0.0.1:8206`，数据库默认为 `maritime_sar.db`。`--db`、`--host`、`--port` 可覆盖默认值。

## 主要接口

写操作使用 JSON，并需要 `X-User` 与 `X-Role` 请求头。角色包括 `coordinator`、`operator`、`field`、`analyst`、`viewer`。

- `GET /health`、`GET /api/state`
- `POST /api/incidents`：创建遇险事件并识别重复报警
- `POST /api/assets`：登记资源
- `POST /api/areas`：创建搜索区域
- `POST /api/assignments`：按能力、海况和航程分配资源，并强制登记出海三件套：
  `expected_return_at`（预计返港，ISO-8601，必须晚于当前）、`report_interval_min`（定时报位间隔，1–1440 分钟）、
  `min_return_margin_km`（最低返航余量/回港线，公里）
- `POST /api/watches/position`：定时报位，必须带经纬度和 `remaining_margin_km`。
  到点未报的装备在下次状态读取时自动进入失联栏（保留最后船位和余量）；失联后报位视为恢复联络并补录；
  余量低于回港线自动转为返航，返航中仍需继续报位
- `POST /api/watches/arrival`：协调员确认进港。进港前装备不能接新任务、不能被撤回或结束区域；
  确认进港后释放搜索区域（回到待分配），装备恢复在港可用
- `POST /api/handovers`、`POST /api/handovers/update`：逐项登记值班交接（事项、待处理原因、关联装备、
  跟进说明、open/following/resolved 状态），写入 SQLite，重开页面继续跟进
- `POST /api/clues`、`POST /api/clues/verify`
- `POST /api/assets/withdraw`：撤回资源并释放任务
- `POST /api/incidents/transfer`、`POST /api/incidents/close`
- `POST /api/offline/batch`：幂等合并离线记录
- `GET /api/incidents/{id}/timeline`

首页（`/`）即“出海值守台”：在航值守、失联栏、返航中分栏展示，支持派出任务、报位/补报、
确认进港和交接事项的逐项跟进，默认每 20 秒自动刷新。值守状态机：
`on_duty → overdue ⇄ on_duty/returning → in_port`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整协调流程、重复报警、错误位置、资源并发占用、离线幂等和权限拒绝。

## 局限

身份依赖调用方传入的用户和角色头；坐标使用球面距离近似；不会自动计算漂移概率区；文件附件、气象服务、真实通信链路和地理围栏未包含在内。
