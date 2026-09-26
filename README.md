# 海上搜救协调系统

标准库实现的独立协调原型，使用 SQLite 保存事件、搜救资源、搜索区域、线索、离线批次和时间线。

首页即出海值守台：派出任务时登记预计返港、定时报位间隔和最低返航余量；到点未报的装备自动进入失联栏并保留最后船位；恢复联络后补报位置和余量，余量低于回港线自动转返航；确认进港前装备不接受新任务；值班交接可逐项登记待处理原因，数据存库，重开页面继续跟进。

## 运行

要求 Python 3.11+（在当前 Python 3.9 环境也可运行）。

```bash
python3 app.py --init --seed
python3 app.py
```

默认地址为 `http://127.0.0.1:8206`，数据库默认为 `maritime_sar.db`。`--db`、`--host`、`--port` 可覆盖默认值。

## 主要接口

写操作使用 JSON，并需要 `X-User` 与 `X-Role` 请求头。角色包括 `coordinator`、`operator`、`field`、`analyst`、`viewer`。

- `GET /health`、`GET /api/state`（读取时自动执行失联巡检）
- `POST /api/incidents`：创建遇险事件并识别重复报警
- `POST /api/assets`：登记资源
- `POST /api/areas`：创建搜索区域
- `POST /api/assignments`：按能力、海况和航程分配资源
- `POST /api/clues`、`POST /api/clues/verify`
- `POST /api/assets/withdraw`：撤回资源并释放任务
- `POST /api/incidents/transfer`、`POST /api/incidents/close`
- `POST /api/offline/batch`：幂等合并离线记录
- `GET /api/incidents/{id}/timeline`

值守台接口：

- `POST /api/missions`：派出任务，登记预计返港时间、报位间隔（分钟）和最低返航余量（公里），装备转为在航
- `POST /api/missions/report`：装备报位（位置 + 剩余余量）；失联装备报位即恢复联络，余量低于回港线自动转返航
- `POST /api/missions/arrive`：确认进港，任务结束且装备恢复可派
- `POST /api/missions/sweep`：手动触发失联巡检（到点未报转失联）
- `POST /api/handover`、`POST /api/handover/close`：逐项登记 / 办结交接班待处理事项

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整协调流程、重复报警、错误位置、资源并发占用、离线幂等、权限拒绝，以及值守台的派出登记、失联巡检、恢复联络补报、低余量转返航、进港前禁派和交接事项。

## 局限

身份依赖调用方传入的用户和角色头；坐标使用球面距离近似；不会自动计算漂移概率区；失联巡检在读取状态或手动触发时执行，无独立后台定时器；文件附件、气象服务、真实通信链路和地理围栏未包含在内。
