import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import DomainError, MaritimeSARService  # noqa: E402


def future_iso(hours=4):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


class MaritimeSARFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = MaritimeSARService(Path(self.tmp.name) / "test.db")
        self.incident = self.service.create_incident(
            "coord1", "coordinator", "SAR-001", "海燕号", 31.0, 122.0, 10.0, 3, "东海中心"
        )
        self.asset = self.service.add_asset(
            "coord1", "coordinator", "海巡01", "vessel", ["surface", "night"], 31.0, 122.0, 20, 100, 5
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_assignment_clue_offline_and_close_flow(self):
        area = self.service.create_search_area(
            "coord1", "coordinator", self.incident["id"], "A-01", "surface", 31.1, 122.1, 8, 1
        )
        assigned = self.service.assign_area("coord1", "coordinator", area["id"], self.asset["id"], self.asset["version"])
        self.assertEqual("assigned", assigned["status"])
        clue = self.service.record_clue(
            "field1", "field", self.incident["id"], "evt-1", 31.1, 122.1, 0.9, "visual", area["id"]
        )
        self.assertEqual("verified", self.service.verify_clue("analyst1", "analyst", clue["id"], "verified")["status"])
        batch = self.service.merge_offline_batch(
            "field1", "field", "batch-1",
            [{"type": "clue", "client_event_id": "off-1", "incident_id": self.incident["id"],
              "latitude": 31.11, "longitude": 122.11, "confidence": 0.7, "source": "radio"}],
        )
        self.assertEqual(1, batch["summary"]["accepted"])
        self.assertTrue(self.service.merge_offline_batch("field1", "field", "batch-1", [])["idempotent"])
        updated_asset = self.service.list_assets()[0]
        self.service.withdraw_asset("coord1", "coordinator", self.asset["id"], "任务移交", updated_asset["version"])
        current_area = self.service.state()["search_areas"][0]
        self.service.complete_area("coord1", "coordinator", area["id"], "abandoned", current_area["version"])
        current_incident = [x for x in self.service.state()["incidents"] if x["id"] == self.incident["id"]][0]
        closed = self.service.close_incident("coord1", "coordinator", self.incident["id"], "resolved", current_incident["version"])
        self.assertEqual("closed", closed["status"])
        self.assertGreaterEqual(len(self.service.incident_timeline(self.incident["id"])), 6)

    def test_duplicate_alarm_and_invalid_position_are_controlled(self):
        duplicate = self.service.create_incident(
            "op1", "operator", "SAR-002", "海燕号", 31.01, 122.01, 5.0, 3, "东海中心"
        )
        self.assertEqual("duplicate", duplicate["status"])
        self.assertEqual(self.incident["id"], duplicate["duplicate_of"])
        invalid = self.service.record_clue(
            "field1", "field", self.incident["id"], "evt-far", 45.0, 130.0, 0.8, "radio"
        )
        self.assertEqual("invalid", invalid["status"])
        with self.assertRaises(DomainError):
            self.service.verify_clue("field1", "field", invalid["id"], "verified")

    def test_assignment_conflict_and_permission(self):
        area = self.service.create_search_area(
            "coord1", "coordinator", self.incident["id"], "A-02", "surface", 31.1, 122.1, 5
        )
        self.service.assign_area("coord1", "coordinator", area["id"], self.asset["id"], self.asset["version"])
        area2 = self.service.create_search_area(
            "coord1", "coordinator", self.incident["id"], "A-03", "surface", 31.2, 122.2, 5
        )
        with self.assertRaises(DomainError) as ctx:
            self.service.assign_area("coord1", "coordinator", area2["id"], self.asset["id"], self.asset["version"])
        self.assertEqual(409, ctx.exception.status)
        with self.assertRaises(DomainError) as ctx2:
            self.service.create_search_area("field1", "field", self.incident["id"], "A-04", "surface", 31, 122, 5)
        self.assertEqual(403, ctx2.exception.status)


class WatchConsoleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = MaritimeSARService(Path(self.tmp.name) / "test.db")
        self.incident = self.service.create_incident(
            "coord1", "coordinator", "SAR-100", "海燕号", 31.0, 122.0, 10.0, 3, "东海中心"
        )
        self.asset = self.service.add_asset(
            "coord1", "coordinator", "海巡01", "vessel", ["surface", "night"], 31.0, 122.0, 20, 100, 5
        )

    def tearDown(self):
        self.tmp.cleanup()

    def dispatch(self, **kw):
        args = dict(asset_id=self.asset["id"], task="扇区搜索", expected_return_at=future_iso(),
                    report_interval_min=15, min_return_margin_km=20.0, incident_id=self.incident["id"])
        args.update(kw)
        return self.service.dispatch_mission("op1", "operator", **args)

    def test_dispatch_registers_watch_terms_and_blocks_retask(self):
        mission = self.dispatch()
        self.assertEqual("active", mission["status"])
        self.assertEqual(20.0, mission["min_return_margin_km"])
        self.assertEqual(15, mission["report_interval_min"])
        self.assertGreater(mission["next_report_due"], mission["last_report_at"])
        self.assertEqual("deployed", self.service.list_assets()[0]["status"])
        with self.assertRaises(DomainError) as ctx:
            self.dispatch()
        self.assertEqual(409, ctx.exception.status)
        area = self.service.create_search_area("coord1", "coordinator", self.incident["id"], "A-10", "surface", 31.1, 122.1, 5)
        with self.assertRaises(DomainError) as ctx2:
            self.service.assign_area("coord1", "coordinator", area["id"], self.asset["id"])
        self.assertEqual(409, ctx2.exception.status)
        with self.assertRaises(DomainError) as ctx3:
            self.service.withdraw_asset("coord1", "coordinator", self.asset["id"], "检修", 2)
        self.assertEqual(409, ctx3.exception.status)

    def test_dispatch_requires_valid_terms(self):
        with self.assertRaises(DomainError):
            self.dispatch(expected_return_at="2020-01-01T00:00:00+00:00")
        with self.assertRaises(DomainError):
            self.dispatch(report_interval_min=0)
        with self.assertRaises(DomainError):
            self.dispatch(min_return_margin_km=150.0)
        with self.assertRaises(DomainError) as ctx:
            self.service.dispatch_mission("f1", "field", self.asset["id"], "任务", future_iso(), 15, 20.0)
        self.assertEqual(403, ctx.exception.status)

    def test_missed_report_enters_lost_column_and_restores_on_report(self):
        mission = self.dispatch()
        with self.service.connect() as conn:
            conn.execute("UPDATE missions SET next_report_due=? WHERE id=?",
                         ("2000-01-01T00:00:00+00:00", mission["id"]))
        state = self.service.state()
        current = [m for m in state["missions"] if m["id"] == mission["id"]][0]
        self.assertEqual("overdue", current["status"])
        self.assertIsNotNone(current["overdue_since"])
        self.assertEqual((31.0, 122.0), (current["last_report_lat"], current["last_report_lon"]))
        restored = self.service.report_mission_position(
            "field1", "field", mission["id"], 31.2, 122.2, 60.0, "恢复联络补报"
        )
        self.assertEqual("active", restored["status"])
        self.assertIsNone(restored["overdue_since"])
        self.assertEqual((31.2, 122.2), (restored["last_report_lat"], restored["last_report_lon"]))
        actions = [t["action"] for t in self.service.state()["timeline"]]
        self.assertIn("mission.overdue", actions)
        self.assertIn("mission.contact_restored", actions)

    def test_low_margin_switches_to_returning_and_arrival_frees_asset(self):
        mission = self.dispatch()
        updated = self.service.report_mission_position("op1", "operator", mission["id"], 31.1, 122.1, 15.0)
        self.assertEqual("returning", updated["status"])
        again = self.service.report_mission_position("op1", "operator", mission["id"], 31.1, 122.1, 30.0)
        self.assertEqual("returning", again["status"])
        with self.assertRaises(DomainError) as ctx:
            self.dispatch()
        self.assertEqual(409, ctx.exception.status)
        arrived = self.service.confirm_arrival("op1", "operator", mission["id"])
        self.assertEqual("arrived", arrived["status"])
        self.assertEqual("available", self.service.list_assets()[0]["status"])
        with self.assertRaises(DomainError):
            self.service.report_mission_position("op1", "operator", mission["id"], 31.0, 122.0, 50.0)
        follow_up = self.dispatch(task="二次出动")
        self.assertEqual("active", follow_up["status"])

    def test_handover_notes_persist_and_close(self):
        mission = self.dispatch()
        note = self.service.add_handover_note("op1", "operator", "余量偏低，下一班重点盯报位", mission["id"])
        self.assertEqual("open", note["status"])
        state = self.service.state()
        saved = [n for n in state["handover_notes"] if n["id"] == note["id"]][0]
        self.assertEqual("海巡01", saved["asset_name"])
        self.assertEqual("余量偏低，下一班重点盯报位", saved["reason"])
        closed = self.service.close_handover_note("op2", "operator", note["id"])
        self.assertEqual("closed", closed["status"])
        self.assertEqual("op2", closed["closed_by"])
        with self.assertRaises(DomainError) as ctx:
            self.service.close_handover_note("op2", "operator", note["id"])
        self.assertEqual(409, ctx.exception.status)
        with self.assertRaises(DomainError):
            self.service.add_handover_note("op1", "operator", "  ", mission["id"])
        with self.assertRaises(DomainError) as ctx2:
            self.service.add_handover_note("f1", "field", "原因", mission["id"])
        self.assertEqual(403, ctx2.exception.status)


if __name__ == "__main__":
    unittest.main()
