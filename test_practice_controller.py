"""Deterministic practice-mode checks; no windows or waits are required."""

import copy
import json
import unittest

from practice_controller import PracticeController
from timer_core import TimerModel


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class PracticeControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.controller = PracticeController(clock=self.clock)

    def test_default_modes_and_independent_countdown_preferences(self):
        controller = self.controller
        self.assertEqual(controller.practice_mode, "question")
        self.assertIs(controller.active_model, controller.question_model)
        self.assertEqual(controller.active_model.target_seconds, 90)
        self.assertFalse(controller.countdown)
        controller.countdown = True
        controller.switch_mode("set")
        self.assertIs(controller.active_model, controller.set_model)
        self.assertEqual(controller.active_model.target_seconds, 7920)
        self.assertTrue(controller.countdown)
        controller.countdown = False
        controller.switch_mode("question")
        self.assertTrue(controller.countdown)
        self.assertFalse(controller.set_countdown)

    def test_132_and_135_minute_presets_measure_hours_and_overtime(self):
        for target in (132 * 60, 135 * 60):
            with self.subTest(target=target):
                clock = FakeClock()
                controller = PracticeController(clock=clock)
                controller.switch_mode("set")
                controller.active_model.set_target(target)
                self.assertTrue(controller.toggle())
                clock.advance(2 * 60 * 60)
                snapshot = controller.active_model.snapshot()
                self.assertEqual(snapshot.question_elapsed_seconds, 7200)
                self.assertEqual(snapshot.remaining_seconds, target - 7200)
                self.assertFalse(snapshot.target_reached)
                clock.advance(target - 7200)
                self.assertTrue(controller.active_model.snapshot().target_reached)
                clock.advance(11.25)
                snapshot = controller.active_model.snapshot()
                self.assertEqual(snapshot.question_elapsed_seconds, target + 11.25)
                self.assertEqual(snapshot.overtime_seconds, 11.25)
                self.assertEqual(snapshot.remaining_seconds, 0)
                self.assertFalse(controller.toggle())
                clock.advance(300)
                self.assertEqual(controller.active_model.question_elapsed_seconds, target + 11.25)

    def test_custom_set_target(self):
        self.controller.switch_mode("set")
        self.controller.active_model.set_target(37 * 60 + 15)
        self.controller.toggle()
        self.clock.advance(37 * 60 + 16)
        self.assertEqual(self.controller.active_model.snapshot().overtime_seconds, 1)
        self.assertEqual(self.controller.question_model.target_seconds, 90)

    def test_switching_pauses_and_preserves_independent_progress_and_records(self):
        controller = self.controller
        controller.toggle()
        self.clock.advance(10)
        question_record = controller.finish_or_next()
        self.clock.advance(4)
        controller.switch_mode("set")
        self.assertFalse(controller.question_model.running)
        self.assertFalse(controller.set_model.running)
        self.assertEqual(controller.question_model.question_elapsed_seconds, 4)
        self.clock.advance(50)
        controller.toggle()
        self.clock.advance(1000)
        controller.switch_mode("question")
        self.assertFalse(controller.question_model.running)
        self.assertFalse(controller.set_model.running)
        self.assertEqual(controller.set_model.question_elapsed_seconds, 1000)
        self.assertEqual(controller.question_model.session_elapsed_seconds, 14)
        self.assertEqual(controller.question_model.completed_records, (question_record,))
        controller.toggle()
        self.clock.advance(2)
        controller.switch_mode("set")
        self.assertEqual(controller.question_model.question_elapsed_seconds, 6)
        self.assertEqual(controller.set_model.question_elapsed_seconds, 1000)

    def test_finish_full_set_pauses_and_records_then_prepares_an_empty_set(self):
        controller = self.controller
        controller.switch_mode("set")
        controller.toggle()
        self.clock.advance(7920.5)
        record = controller.finish_or_next()
        self.assertEqual(record.question_number, 1)
        self.assertEqual(record.elapsed_seconds, 7920.5)
        self.assertEqual(record.target_seconds, 7920)
        self.assertFalse(record.on_target)
        self.assertFalse(controller.active_model.running)
        self.assertEqual(controller.active_model.current_question_number, 2)
        self.assertEqual(controller.active_model.question_elapsed_seconds, 0)
        self.assertEqual(controller.active_model.completed_records, (record,))
        self.clock.advance(100)
        self.assertEqual(controller.active_model.session_elapsed_seconds, 7920.5)
        self.assertIsNone(controller.finish_or_next())
        controller.toggle()
        self.clock.advance(30)
        controller.toggle()
        second = controller.finish_or_next()
        self.assertEqual(second.elapsed_seconds, 30)
        self.assertFalse(controller.active_model.running)

    def test_next_question_preserves_running_or_paused_state(self):
        self.controller.toggle()
        self.clock.advance(2)
        self.controller.finish_or_next()
        self.assertTrue(self.controller.active_model.running)
        self.clock.advance(3)
        self.controller.toggle()
        self.controller.finish_or_next()
        self.assertFalse(self.controller.active_model.running)
        self.assertEqual(len(self.controller.active_model.completed_records), 2)

    def test_empty_guard_does_not_change_running_state_or_records(self):
        for mode in ("question", "set"):
            with self.subTest(mode=mode):
                self.controller.switch_mode(mode)
                self.assertIsNone(self.controller.finish_or_next())
                self.controller.toggle()
                self.clock.advance(0.049)
                self.assertIsNone(self.controller.finish_or_next())
                self.assertTrue(self.controller.active_model.running)
                self.assertEqual(self.controller.active_model.completed_records, ())
                self.clock.advance(0.01)
                self.assertIsNotNone(self.controller.finish_or_next())

    def test_reset_active_does_not_touch_other_mode(self):
        controller = self.controller
        controller.toggle()
        self.clock.advance(12)
        controller.finish_or_next()
        self.clock.advance(4)
        controller.switch_mode("set")
        question_state = controller.question_model.to_dict()
        controller.active_model.set_target(135 * 60)
        controller.toggle()
        self.clock.advance(500)
        controller.finish_or_next()
        controller.toggle()
        self.clock.advance(30)
        controller.reset_active()
        self.assertEqual(controller.question_model.to_dict(), question_state)
        self.assertEqual(controller.set_model.session_elapsed_seconds, 0)
        self.assertEqual(controller.set_model.completed_records, ())
        self.assertEqual(controller.set_model.target_seconds, 135 * 60)
        self.assertFalse(controller.set_model.running)
        set_state = controller.set_model.to_dict()
        controller.switch_mode("question")
        controller.reset_active()
        self.assertEqual(controller.question_model.session_elapsed_seconds, 0)
        self.assertEqual(controller.set_model.to_dict(), set_state)

    def test_legacy_load_preserves_question_mode_and_starts_default_empty_set(self):
        legacy = TimerModel(clock=self.clock)
        legacy.start()
        self.clock.advance(5)
        legacy.next_question()
        self.clock.advance(9)
        state = {"session": legacy.to_dict(), "countdown": True, "topmost": True}
        self.controller.switch_mode("set")
        self.controller.active_model.set_target(135 * 60)
        self.controller.toggle()
        self.clock.advance(30)
        self.controller.restore(state)
        self.assertEqual(self.controller.practice_mode, "question")
        self.assertTrue(self.controller.countdown)
        self.assertFalse(self.controller.question_model.running)
        self.assertEqual(self.controller.question_model.session_elapsed_seconds, 14)
        self.assertEqual(self.controller.question_model.question_elapsed_seconds, 9)
        self.assertEqual(len(self.controller.question_model.completed_records), 1)
        self.assertEqual(self.controller.set_model.target_seconds, 7920)
        self.assertEqual(self.controller.set_model.session_elapsed_seconds, 0)
        self.assertTrue(self.controller.set_countdown)
        self.clock.advance(100)
        self.assertEqual(self.controller.question_model.session_elapsed_seconds, 14)

    def test_roundtrip_both_modes_and_countdown_preferences_always_pauses(self):
        controller = self.controller
        controller.countdown = True
        controller.toggle()
        self.clock.advance(5)
        controller.finish_or_next()
        self.clock.advance(3)
        controller.switch_mode("set")
        controller.active_model.set_target(135 * 60)
        controller.countdown = False
        controller.toggle()
        self.clock.advance(30)
        controller.finish_or_next()
        controller.toggle()
        self.clock.advance(20)
        saved = json.loads(json.dumps(controller.to_dict()))
        self.clock.advance(500)
        restored = PracticeController(clock=self.clock)
        restored.restore(saved)
        self.assertEqual(restored.to_dict(), saved)
        self.assertEqual(restored.practice_mode, "set")
        self.assertFalse(restored.countdown)
        self.assertTrue(restored.question_countdown)
        self.assertFalse(restored.question_model.running)
        self.assertFalse(restored.set_model.running)
        self.clock.advance(100)
        self.assertEqual(restored.to_dict(), saved)
        restored.toggle()
        self.clock.advance(10)
        self.assertEqual(restored.set_model.question_elapsed_seconds, 30)
        self.assertEqual(restored.question_model.session_elapsed_seconds, 8)

    def test_malformed_restore_is_atomic_and_does_not_pause_existing_clock(self):
        controller = self.controller
        controller.toggle()
        self.clock.advance(4)
        original = controller.to_dict()
        question_model = controller.question_model
        set_model = controller.set_model
        bad_states = [None, [], {}, {"session": original["session"], "countdown": 1}]
        for field, value in (("practice_mode", "other"), ("practice_mode", 1),
                             ("question_countdown", "yes"), ("set_countdown", None),
                             ("set_session", {})):
            bad_states.append({**original, field: value})
        partial = copy.deepcopy(original)
        del partial["set_countdown"]
        bad_states.append(partial)
        inconsistent = copy.deepcopy(original)
        inconsistent["set_session"]["session_elapsed_seconds"] = 1
        bad_states.append(inconsistent)
        for bad in bad_states:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    controller.restore(bad)
                self.assertIs(controller.question_model, question_model)
                self.assertIs(controller.set_model, set_model)
                self.assertTrue(controller.question_model.running)
                self.assertEqual(controller.to_dict(), original)

    def test_invalid_mode_and_countdown_changes_leave_state_unchanged(self):
        self.controller.toggle()
        self.clock.advance(4)
        original = self.controller.to_dict()
        for bad in (None, True, 1, [], "SET", "other"):
            with self.subTest(mode=bad):
                with self.assertRaises(ValueError):
                    self.controller.switch_mode(bad)
                self.assertTrue(self.controller.question_model.running)
                self.assertEqual(self.controller.to_dict(), original)
        for bad in (None, 0, 1, "false"):
            with self.subTest(countdown=bad):
                with self.assertRaises(ValueError):
                    self.controller.countdown = bad
                self.assertEqual(self.controller.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
