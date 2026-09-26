import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import DomainError, MaritimeSARService  # noqa: E402


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class WatchLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "watch.db"
        self.service = MaritimeSARService(self.db)
        self.incident = self.service.create_incident(
            "coord1", "coordinator", "SAR-W1", "长风号", 31.0, 122.0, 10.0, 3, "东海中心"
        )
        self.asset = self.service.add_asset(
            "coord1", "coordinator", "海巡W1", "vessel", ["surface", "night"], 31.0, 122.0, 20, 100, 5
        )
        self.aircraft = self.service.add_asset(
            "coord1", "coordinator", "救助直升机W", "aircraft", ["air", "night"], 31.0, 122.0, 180, 260, 5
        )
        self.area = self.service.create_search_area(
            "coord1", "coordinator", self.incident["id"], "AW-1", "surface", 31.1, 122.1, 8, 1
        )
        self.eta = iso(datetime.now(timezone.utc) + timedelta(hours=4))
        assigned = self.service.assign_area(
            "coord1", "coordinator", self.area["id"], self.asset["id"], self.eta, 60, 40.0
        )
        self.watch_id = assigned["watch_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatch_registers_eta_report_and_margin_plan(self):
        state = self.service.state()
        watch = [w for w in state["watches"] if w["id"] == self.watch_id][0]
        self.assertEqual("on_duty", watch["status"])
        self.assertEqual(60, watch["report_interval_min"])
        self.assertEqual(40.0, watch["min_return_margin_km"])
        self.assertEqual(self.eta, watch["eta_return"])
        self.assertIsNotNone(watch["next_report_due"])
        # 出海中的装备不能直接撤回
        current_asset = [a for a in state["assets"] if a["id"] == self.asset["id"]][0]
        with self.assertRaises(DomainError) as ctx:
            self.service.withdraw_asset("coord1", "coordinator", self.asset["id"], "换船", current_asset["version"])
        self.assertEqual(409, ctx.exception.status)
        self.assertIn("进港", str(ctx.exception))

    def test_overdue_keeps_last_position_and_report_restores(self):
        # 先报一次位，记录最后船位
        self.service.report_position("op1", "operator", self.asset["id"], 31.21, 122.22, 80.0, "航行正常")
        # 手工把下次报位期限挪到过去，模拟到点未报
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE asset_watches SET next_report_due=? WHERE id=?",
                         (iso(datetime.now(timezone.utc) - timedelta(minutes=1)), self.watch_id))
        state = self.service.state()
        watch = [w for w in state["watches"] if w["id"] == self.watch_id][0]
        self.assertEqual("overdue", watch["status"])
        self.assertAlmostEqual(31.21, watch["last_lat"], places=6)
        self.assertAlmostEqual(122.22, watch["last_lon"], places=6)
        self.assertEqual(80.0, watch["last_margin_km"])
        self.assertIsNotNone(watch["overdue_since"])
        # 恢复联络必须补报位置和余量
        with self.assertRaises(DomainError):
            self.service.report_position("op1", "operator", self.asset["id"], 31.21, 122.22, -1)
        restored = self.service.report_position("op1", "operator", self.asset["id"], 31.30, 122.30, 75.0, "电台恢复")
        self.assertEqual("on_duty", restored["status"])
        self.assertIsNone(restored["overdue_since"])
        actions = [x["action"] for x in self.service.incident_timeline(self.incident["id"])]
        self.assertIn("watch.overdue", actions)
        self.assertIn("watch.contact_restored", actions)

    def test_low_margin_forces_returning_and_blocks_new_tasks(self):
        result = self.service.report_position("op1", "operator", self.asset["id"], 31.2, 122.2, 35.0)
        self.assertEqual("returning", result["status"])
        # 返航中的装备不能接新任务：资源状态仍非 available
        area2 = self.service.create_search_area(
            "coord1", "coordinator", self.incident["id"], "AW-2", "surface", 31.2, 122.2, 5
        )
        with self.assertRaises(DomainError) as ctx:
            self.service.assign_area("coord1", "coordinator", area2["id"], self.asset["id"], self.eta, 60, 40.0)
        self.assertEqual(409, ctx.exception.status)
        # 返航仍需继续报位；到点未报一样失联
        self.service.report_position("op1", "operator", self.asset["id"], 31.1, 122.1, 30.0)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE asset_watches SET next_report_due=? WHERE id=?",
                         (iso(datetime.now(timezone.utc) - timedelta(minutes=2)), self.watch_id))
        state = self.service.state()
        watch = [w for w in state["watches"] if w["id"] == self.watch_id][0]
        self.assertEqual("overdue", watch["status"])
        # 失联补报余量仍低于回港线时，恢复后直接是返航
        restored = self.service.report_position("op1", "operator", self.asset["id"], 31.05, 122.05, 28.0)
        self.assertEqual("returning", restored["status"])
        # 确认进港后才能再次派任务
        arrived = self.service.confirm_arrival("coord1", "coordinator", self.asset["id"])
        self.assertEqual("in_port", arrived["status"])
        state = self.service.state()
        asset = [a for a in state["assets"] if a["id"] == self.asset["id"]][0]
        self.assertEqual("available", asset["status"])
        old_area = [a for a in state["search_areas"] if a["id"] == self.area["id"]][0]
        self.assertEqual("planned", old_area["status"])
        self.service.assign_area("coord1", "coordinator", area2["id"], self.asset["id"], self.eta, 60, 40.0)

    def test_no_report_without_active_watch(self):
        with self.assertRaises(DomainError) as ctx:
            self.service.report_position("op1", "operator", self.aircraft["id"], 31.0, 122.0, 50.0)
        self.assertEqual(409, ctx.exception.status)

    def test_handover_items_persist_and_can_be_followed(self):
        self.service.create_handover(
            "op-day", "operator", "夜班会守海巡W1报位", "该船电台曾有杂音，漏报先拨卫星电话",
            asset_id=self.asset["id"]
        )
        item = self.service.create_handover(
            "op-day", "operator", "直升机油量复核", "起飞前确认最低返航余量 60 公里"
        )
        self.assertIsNone(item["watch_id"])
        state = self.service.state()
        opens = [h for h in state["handovers"] if h["status"] == "open"]
        self.assertEqual(2, len(opens))
        # 重开页面（新服务实例，同一数据库）仍能读到
        reopened = MaritimeSARService(self.db).state()
        persisted = [h for h in reopened["handovers"] if h["title"] == "夜班会守海巡W1报位"][0]
        updated = self.service.update_handover(
            "op-night", "coordinator", persisted["id"], status="following", follow_up="已拨打卫星电话，约定 22:00 报位"
        )
        self.assertEqual("following", updated["status"])
        self.assertIn("22:00", updated["follow_up"])
        self.service.update_handover("op-night", "coordinator", persisted["id"], status="resolved",
                                     follow_up="已按时报位，事项关闭")
        with self.assertRaises(DomainError):
            self.service.update_handover("op-night", "coordinator", persisted["id"], status="bogus")

    def test_dispatch_requires_full_plan_and_future_eta(self):
        area = self.service.create_search_area(
            "coord1", "coordinator", self.incident["id"], "AW-3", "air", 31.2, 122.2, 5
        )
        past = iso(datetime.now(timezone.utc) - timedelta(hours=1))
        with self.assertRaises(DomainError):
            self.service.assign_area("coord1", "coordinator", area["id"], self.aircraft["id"], past, 60, 40.0)
        with self.assertRaises(DomainError):
            self.service.assign_area("coord1", "coordinator", area["id"], self.aircraft["id"], self.eta, 0, 40.0)
        with self.assertRaises(DomainError):
            self.service.assign_area("coord1", "coordinator", area["id"], self.aircraft["id"], self.eta, 60, -5.0)


if __name__ == "__main__":
    unittest.main()
