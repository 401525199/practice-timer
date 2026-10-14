"""UI-independent timing state for the CFA practice timer."""

from dataclasses import dataclass
from math import isclose, isfinite
from time import monotonic
from typing import Callable, Tuple


@dataclass(frozen=True)
class QuestionRecord:
    question_number: int
    elapsed_seconds: float
    target_seconds: float

    @property
    def on_target(self) -> bool:
        return self.elapsed_seconds <= self.target_seconds


@dataclass(frozen=True)
class TimerSnapshot:
    running: bool
    question_number: int
    question_elapsed_seconds: float
    session_elapsed_seconds: float
    target_seconds: float
    remaining_seconds: float
    overtime_seconds: float
    completed_count: int

    @property
    def target_reached(self) -> bool:
        return self.question_elapsed_seconds >= self.target_seconds


class TimerModel:
    """Count active practice time without including paused intervals.

    ``clock`` must return monotonically increasing seconds. Supplying a custom
    clock makes all timing behavior deterministic in tests.
    """

    def __init__(
        self,
        target_seconds: float = 90,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._clock = clock
        self._target_seconds = self._validated_target(target_seconds)
        self._running = False
        self._started_at = 0.0
        self._question_accumulated = 0.0
        self._completed_elapsed = 0.0
        self._records = []

    @staticmethod
    def _validated_target(seconds: float) -> float:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise ValueError("Target seconds must be a finite number >= 1.") from None
        if not isfinite(seconds) or seconds < 1:
            raise ValueError("Target seconds must be a finite number >= 1.")
        return seconds

    @property
    def running(self) -> bool:
        return self._running

    @property
    def target_seconds(self) -> float:
        return self._target_seconds

    @property
    def current_question_number(self) -> int:
        return len(self._records) + 1

    @property
    def completed_records(self) -> Tuple[QuestionRecord, ...]:
        return tuple(self._records)

    def _elapsed_at(self, now: float) -> float:
        if self._running:
            return self._question_accumulated + max(0.0, now - self._started_at)
        return self._question_accumulated

    @property
    def question_elapsed_seconds(self) -> float:
        return self._elapsed_at(self._clock())

    @property
    def session_elapsed_seconds(self) -> float:
        return self._completed_elapsed + self.question_elapsed_seconds

    def snapshot(self) -> TimerSnapshot:
        elapsed = self._elapsed_at(self._clock())
        return TimerSnapshot(
            running=self._running,
            question_number=self.current_question_number,
            question_elapsed_seconds=elapsed,
            session_elapsed_seconds=self._completed_elapsed + elapsed,
            target_seconds=self._target_seconds,
            remaining_seconds=max(0.0, self._target_seconds - elapsed),
            overtime_seconds=max(0.0, elapsed - self._target_seconds),
            completed_count=len(self._records),
        )

    def start(self) -> None:
        if not self._running:
            self._started_at = self._clock()
            self._running = True

    def pause(self) -> None:
        if self._running:
            self._question_accumulated = self._elapsed_at(self._clock())
            self._running = False

    def toggle(self) -> bool:
        if self._running:
            self.pause()
        else:
            self.start()
        return self._running

    def next_question(self) -> QuestionRecord:
        """Record the current question, then open another in the same run state."""
        now = self._clock()
        elapsed = self._elapsed_at(now)
        record = QuestionRecord(
            question_number=self.current_question_number,
            elapsed_seconds=elapsed,
            target_seconds=self._target_seconds,
        )
        self._records.append(record)
        self._completed_elapsed += elapsed
        self._question_accumulated = 0.0
        self._started_at = now
        return record

    def reset(self) -> None:
        """Begin an empty, paused session while retaining the selected target."""
        self._running = False
        self._started_at = 0.0
        self._question_accumulated = 0.0
        self._completed_elapsed = 0.0
        self._records.clear()

    def set_target(self, seconds: float) -> None:
        self._target_seconds = self._validated_target(seconds)

    def to_dict(self) -> dict:
        """Return a JSON-compatible snapshot using one clock sample."""
        snapshot = self.snapshot()
        return {
            "version": 1,
            "target_seconds": self._target_seconds,
            "question_elapsed_seconds": snapshot.question_elapsed_seconds,
            "session_elapsed_seconds": snapshot.session_elapsed_seconds,
            "completed_records": [
                {
                    "question_number": record.question_number,
                    "elapsed_seconds": record.elapsed_seconds,
                    "target_seconds": record.target_seconds,
                }
                for record in self._records
            ],
        }

    @staticmethod
    def _stored_number(value: object, minimum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Saved timing values must be numeric.")
        number = float(value)
        if not isfinite(number) or number < minimum:
            raise ValueError("Saved timing values are outside the allowed range.")
        return number

    def restore(self, data: dict) -> None:
        """Restore a valid saved session in a paused state, or change nothing.

        Time spent outside the app is deliberately excluded. The redundant
        session total is checked to detect an incomplete or inconsistent save.
        """
        try:
            if not isinstance(data, dict) or type(data.get("version")) is not int:
                raise ValueError("Invalid saved session format.")
            if data["version"] != 1:
                raise ValueError("Unsupported saved session version.")
            target = self._stored_number(data["target_seconds"], 1)
            question_elapsed = self._stored_number(data["question_elapsed_seconds"], 0)
            session_elapsed = self._stored_number(data["session_elapsed_seconds"], 0)
            saved_records = data["completed_records"]
            if not isinstance(saved_records, list):
                raise ValueError("Saved question records must be a list.")
            records = []
            completed_elapsed = 0.0
            for expected_number, saved in enumerate(saved_records, start=1):
                if not isinstance(saved, dict):
                    raise ValueError("Invalid saved question record.")
                number = saved["question_number"]
                if type(number) is not int or number != expected_number:
                    raise ValueError("Saved questions must be numbered sequentially.")
                elapsed = self._stored_number(saved["elapsed_seconds"], 0)
                record_target = self._stored_number(saved["target_seconds"], 1)
                records.append(QuestionRecord(number, elapsed, record_target))
                completed_elapsed += elapsed
            total = completed_elapsed + question_elapsed
            if not isfinite(total) or not isclose(total, session_elapsed, rel_tol=1e-9, abs_tol=1e-6):
                raise ValueError("Saved session total is inconsistent.")
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError("Invalid saved session format.") from error

        # Apply only after every field has passed validation.
        self._target_seconds = target
        self._question_accumulated = question_elapsed
        self._completed_elapsed = completed_elapsed
        self._records = records
        self._started_at = 0.0
        self._running = False
