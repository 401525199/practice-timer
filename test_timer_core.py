"""Run with ``python -m unittest test_timer_core``; no GUI is required."""

import copy
import json
import unittest

from timer_core import TimerModel


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TimerModelTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.timer = TimerModel(target_seconds=90, clock=self.clock)

    def test_initial_state_and_elapsed_clock(self):
        self.clock.advance(50)
        self.assertEqual(self.timer.question_elapsed_seconds, 0)
        self.assertEqual(self.timer.current_question_number, 1)
        self.assertFalse(self.timer.running)
        self.timer.start()
        self.clock.advance(12.25)
        self.assertEqual(self.timer.question_elapsed_seconds, 12.25)
        self.assertEqual(self.timer.session_elapsed_seconds, 12.25)
        self.timer.start()  # Starting a running clock does not lose time.
        self.clock.advance(1)
        self.assertEqual(self.timer.question_elapsed_seconds, 13.25)

    def test_pause_excludes_paused_interval_and_resume_accumulates(self):
        self.timer.start()
        self.clock.advance(8.5)
        self.timer.pause()
        self.clock.advance(200)
        self.timer.pause()
        self.assertEqual(self.timer.question_elapsed_seconds, 8.5)
        self.assertEqual(self.timer.session_elapsed_seconds, 8.5)
        self.assertTrue(self.timer.toggle())
        self.clock.advance(2.5)
        self.assertFalse(self.timer.toggle())
        self.assertEqual(self.timer.question_elapsed_seconds, 11)

    def test_next_question_preserves_running_and_session_elapsed(self):
        self.timer.start()
        self.clock.advance(16)
        record = self.timer.next_question()
        self.assertEqual(record.question_number, 1)
        self.assertEqual(record.elapsed_seconds, 16)
        self.assertEqual(record.target_seconds, 90)
        self.assertTrue(record.on_target)
        self.assertTrue(self.timer.running)
        self.assertEqual(self.timer.current_question_number, 2)
        self.assertEqual(self.timer.question_elapsed_seconds, 0)
        self.assertEqual(self.timer.session_elapsed_seconds, 16)
        self.clock.advance(7)
        self.assertEqual(self.timer.question_elapsed_seconds, 7)
        self.assertEqual(self.timer.session_elapsed_seconds, 23)
        self.assertEqual(self.timer.completed_records, (record,))

    def test_next_question_preserves_pause(self):
        self.timer.start()
        self.clock.advance(10)
        self.timer.pause()
        self.clock.advance(100)
        self.timer.next_question()
        self.clock.advance(100)
        self.assertFalse(self.timer.running)
        self.assertEqual(self.timer.question_elapsed_seconds, 0)
        self.assertEqual(self.timer.session_elapsed_seconds, 10)

    def test_reset_stops_and_clears_session_but_retains_target(self):
        self.timer.set_target(120)
        self.timer.start()
        self.clock.advance(25)
        self.timer.next_question()
        self.clock.advance(3)
        self.timer.reset()
        self.clock.advance(10)
        self.assertFalse(self.timer.running)
        self.assertEqual(self.timer.target_seconds, 120)
        self.assertEqual(self.timer.current_question_number, 1)
        self.assertEqual(self.timer.completed_records, ())
        self.assertEqual(self.timer.session_elapsed_seconds, 0)
        self.timer.start()
        self.clock.advance(4)
        self.assertEqual(self.timer.question_elapsed_seconds, 4)

    def test_target_boundary_overtime_and_record_target_preservation(self):
        self.timer.set_target(10)
        self.timer.start()
        self.clock.advance(9)
        snapshot = self.timer.snapshot()
        self.assertEqual(snapshot.remaining_seconds, 1)
        self.assertFalse(snapshot.target_reached)
        self.clock.advance(1)
        snapshot = self.timer.snapshot()
        self.assertTrue(snapshot.target_reached)
        self.assertEqual(snapshot.overtime_seconds, 0)
        self.clock.advance(2.5)
        snapshot = self.timer.snapshot()
        self.assertEqual(snapshot.remaining_seconds, 0)
        self.assertEqual(snapshot.overtime_seconds, 2.5)
        record = self.timer.next_question()
        self.assertFalse(record.on_target)
        self.timer.set_target(20)
        self.assertEqual(record.target_seconds, 10)
        self.assertEqual(self.timer.snapshot().target_seconds, 20)

    def test_rejects_invalid_targets_without_changing_existing_target(self):
        for invalid in (0, -1, 0.9, float("inf"), float("nan"), None, "x"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.timer.set_target(invalid)
                self.assertEqual(self.timer.target_seconds, 90)
                with self.assertRaises(ValueError):
                    TimerModel(target_seconds=invalid, clock=self.clock)
        self.timer.set_target(1)
        self.assertEqual(self.timer.target_seconds, 1)

    def test_save_restore_roundtrip_pauses_and_excludes_closed_time(self):
        self.timer.start()
        self.clock.advance(12.5)
        self.timer.next_question()
        self.timer.set_target(120)
        self.clock.advance(8.25)
        data = json.loads(json.dumps(self.timer.to_dict()))
        self.clock.advance(400)
        reopened = TimerModel(clock=self.clock)
        reopened.restore(data)
        self.assertFalse(reopened.running)
        self.assertEqual(reopened.target_seconds, 120)
        self.assertEqual(reopened.current_question_number, 2)
        self.assertEqual(reopened.question_elapsed_seconds, 8.25)
        self.assertEqual(reopened.session_elapsed_seconds, 20.75)
        self.assertEqual(reopened.completed_records, self.timer.completed_records)
        self.clock.advance(50)
        self.assertEqual(reopened.session_elapsed_seconds, 20.75)
        reopened.start()
        self.clock.advance(2)
        self.assertEqual(reopened.session_elapsed_seconds, 22.75)

    def test_malformed_restore_leaves_running_session_unchanged(self):
        self.timer.start()
        self.clock.advance(10)
        self.timer.next_question()
        self.clock.advance(5)
        valid = self.timer.to_dict()
        invalid_data = [None, [], {}, {**valid, "version": 2}]
        for field, value in (
            ("target_seconds", 0),
            ("target_seconds", float("inf")),
            ("question_elapsed_seconds", -1),
            ("question_elapsed_seconds", True),
            ("session_elapsed_seconds", float("nan")),
            ("session_elapsed_seconds", 16),
            ("completed_records", {}),
        ):
            invalid_data.append({**valid, field: value})
        for field, value in (
            ("question_number", 2),
            ("question_number", True),
            ("elapsed_seconds", -1),
            ("target_seconds", "90"),
        ):
            bad = copy.deepcopy(valid)
            bad["completed_records"][0][field] = value
            invalid_data.append(bad)
        for bad in invalid_data:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.timer.restore(bad)
                self.assertTrue(self.timer.running)
                self.assertEqual(self.timer.to_dict(), valid)

    def test_zero_elapsed_records_are_preserved(self):
        record = self.timer.next_question()
        self.assertEqual(record.elapsed_seconds, 0)
        data = self.timer.to_dict()
        other = TimerModel(clock=self.clock)
        other.restore(data)
        self.assertEqual(other.completed_records, (record,))
        self.assertEqual(other.current_question_number, 2)


if __name__ == "__main__":
    unittest.main()
