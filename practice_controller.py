"""Independent per-question and full-set practice timing state."""

from time import monotonic
from typing import Callable, Optional

from timer_core import QuestionRecord, TimerModel


class PracticeController:
    """Keep both practice modes while allowing only the selected mode to run.

    A full set uses the existing TimerModel's question slot as its current set
    and question records as completed sets. Switching modes pauses both clocks.
    Durations are set through each model's existing ``set_target`` method.
    """

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self.question_model = TimerModel(target_seconds=90, clock=clock)
        self.set_model = TimerModel(target_seconds=132 * 60, clock=clock)
        self._practice_mode = "question"
        self.question_countdown = False
        self.set_countdown = True

    @staticmethod
    def _validated_mode(mode: str) -> str:
        if not isinstance(mode, str) or mode not in ("question", "set"):
            raise ValueError("Practice mode must be 'question' or 'set'.")
        return mode

    @staticmethod
    def _validated_countdown(value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("Countdown preference must be a boolean.")
        return value

    @property
    def practice_mode(self) -> str:
        return self._practice_mode

    @property
    def active_model(self) -> TimerModel:
        return self.question_model if self._practice_mode == "question" else self.set_model

    @property
    def countdown(self) -> bool:
        return self.question_countdown if self._practice_mode == "question" else self.set_countdown

    @countdown.setter
    def countdown(self, value: bool) -> None:
        value = self._validated_countdown(value)
        if self._practice_mode == "question":
            self.question_countdown = value
        else:
            self.set_countdown = value

    def switch_mode(self, mode: str) -> None:
        mode = self._validated_mode(mode)
        self.question_model.pause()
        self.set_model.pause()
        self._practice_mode = mode

    def toggle(self) -> bool:
        return self.active_model.toggle()

    def finish_or_next(self) -> Optional[QuestionRecord]:
        """Record a question and continue, or finish a set and pause its next set."""
        model = self.active_model
        if model.question_elapsed_seconds < 0.05:
            return None
        if self._practice_mode == "set":
            model.pause()
        return model.next_question()

    def reset_active(self) -> None:
        self.active_model.reset()

    def to_dict(self) -> dict:
        """Return the timing portion of the outer application save state."""
        return {
            "session": self.question_model.to_dict(),
            "set_session": self.set_model.to_dict(),
            "practice_mode": self._practice_mode,
            "question_countdown": self.question_countdown,
            "set_countdown": self.set_countdown,
        }

    def restore(self, state: dict) -> None:
        """Restore both modes paused, accepting old per-question application saves.

        Old saves contain ``session`` and optionally ``countdown``. Full-set
        timing then starts empty at 132 minutes, with its countdown enabled.
        A new save must contain all five timing fields; malformed input leaves
        the current controller, including any running clocks, unchanged.
        Successful restoration replaces the two model references.
        """
        if not isinstance(state, dict):
            raise ValueError("Saved practice state must be a dictionary.")
        new_fields = ("set_session", "practice_mode", "question_countdown", "set_countdown")
        try:
            question_model = TimerModel(target_seconds=90, clock=self._clock)
            set_model = TimerModel(target_seconds=132 * 60, clock=self._clock)
            question_model.restore(state["session"])
            if any(field in state for field in new_fields):
                mode = self._validated_mode(state["practice_mode"])
                question_countdown = self._validated_countdown(state["question_countdown"])
                set_countdown = self._validated_countdown(state["set_countdown"])
                set_model.restore(state["set_session"])
            else:
                mode = "question"
                question_countdown = self._validated_countdown(state.get("countdown", False))
                set_countdown = True
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError("Saved practice state is incomplete or malformed.") from error

        # Commit only after both sessions and every preference pass validation.
        self.question_model = question_model
        self.set_model = set_model
        self._practice_mode = mode
        self.question_countdown = question_countdown
        self.set_countdown = set_countdown
