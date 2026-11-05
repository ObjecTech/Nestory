"""One GPU owner, chat first, latest pending vision and 120s vision fallback."""
from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass

from .assistant_common import AssistantError


@dataclass(eq=False)
class Ticket:
    kind: str
    submitted: float
    expires: float
    superseded: bool = False


class InferenceScheduler:
    def __init__(self, *, chat_capacity=2, chat_budget=8, clock=time.monotonic):
        self.cv = threading.Condition()
        self.waiting = {'vision': deque(), 'chat': deque()}
        self.capacity = {'vision': 1, 'chat': chat_capacity}
        self.running = None
        self.clock, self.chat_budget = clock, chat_budget
        self.last_vision_success = None
        self.accepting = True
        self.completed = {'vision': 0, 'chat': 0}
        self.failures = self.chat_overruns = self.vision_replaced = 0

    def stop_accepting(self):
        """Reject new/queued jobs while leaving the active GPU owner untouched."""
        with self.cv:
            self.accepting = False
            self.cv.notify_all()

    def _pick(self):
        vision, chat = self.waiting['vision'], self.waiting['chat']
        overdue = (self.last_vision_success is None or
                   self.clock() - self.last_vision_success >= 120)
        if vision and overdue:
            return vision[0]
        if chat:
            return chat[0]
        return vision[0] if vision else None

    @contextmanager
    def slot(self, kind, timeout=None):
        if kind not in self.waiting:
            raise ValueError('Unknown inference kind')
        submitted = self.clock()
        timeout = (10 if kind == 'vision' else 30) if timeout is None else timeout
        ticket = Ticket(kind, submitted, submitted + timeout)
        with self.cv:
            if not self.accepting:
                raise AssistantError('model_unavailable', 503, retryable=True)
            waiting = self.waiting[kind]
            if kind == 'vision' and waiting:
                # Only a waiting request is replaced. Its caller gets a failure;
                # it cannot apply an old frame or accidentally release the owner.
                waiting.popleft().superseded = True
                self.vision_replaced += 1
            elif len(waiting) >= self.capacity[kind]:
                raise AssistantError('queue_full', 429, retryable=True, retry_after=2)
            waiting.append(ticket)
            self.cv.notify_all()
            try:
                while True:
                    if ticket.superseded:
                        raise AssistantError('vision_superseded', 409, retryable=True)
                    if not self.accepting:
                        raise AssistantError('model_unavailable', 503, retryable=True)
                    remaining = ticket.expires - self.clock()
                    if remaining <= 0:
                        raise AssistantError('model_timeout', 504, retryable=True)
                    if self.running is None and self._pick() is ticket:
                        break
                    self.cv.wait(min(remaining, .1))
                waiting.popleft()
                self.running = ticket
            except BaseException:
                if ticket in waiting:
                    waiting.remove(ticket)
                self.cv.notify_all()
                raise
        started = self.clock()
        succeeded = False
        try:
            yield max(0, round((started - submitted) * 1000))
            succeeded = True
        except BaseException:
            with self.cv:
                self.failures += 1
            raise
        finally:
            with self.cv:
                if kind == 'chat' and self.clock() - started >= self.chat_budget:
                    self.chat_overruns += 1
                if kind == 'vision' and succeeded:
                    self.last_vision_success = self.clock()
                self.completed[kind] += 1
                self.running = None
                self.cv.notify_all()

    def metrics(self):
        with self.cv:
            age = (None if self.last_vision_success is None else
                   round(max(0, self.clock() - self.last_vision_success), 3))
            return dict(running_kind=self.running.kind if self.running else None,
                        pending={k: len(v) for k, v in self.waiting.items()},
                        completed=dict(self.completed), failures=self.failures,
                        chat_overruns=self.chat_overruns, vision_replaced=self.vision_replaced,
                        vision_success_age_seconds=age, accepting=self.accepting,
                        policy='chat_first_latest_vision_120s')
