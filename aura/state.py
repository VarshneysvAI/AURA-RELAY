"""
AURA Relay State Manager - Thread-safe application state
"""
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class Event:
    """Represents a single event in the transparency timeline."""
    timestamp: str
    event_type: str
    message: str
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "type": self.event_type,
            "message": self.message,
            "details": self.details or {}
        }


@dataclass
class Screenshot:
    """Represents a captured screenshot."""
    filename: str
    timestamp: str
    step: str
    path: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "timestamp": self.timestamp,
            "step": self.step,
            "path": self.path
        }


@dataclass 
class AppState:
    """Application state container."""
    status: str = "idle"  # idle, running, paused, done, error, stopped
    instruction: str = ""
    plan: List[str] = field(default_factory=list)
    current_step: str = ""
    next_step: str = ""
    reason: str = ""
    events: List[Event] = field(default_factory=list)
    screenshots: List[Screenshot] = field(default_factory=list)
    question: Optional[str] = None
    question_id: Optional[int] = None
    result: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class StateManager:
    """Thread-safe state manager with event subscription support."""
    
    def __init__(self):
        self._state = AppState()
        self._lock = threading.RLock()
        self._subscribers: List[Callable[[AppState], None]] = []
        self._question_counter = 0
    
    def subscribe(self, callback: Callable[[AppState], None]) -> None:
        """Subscribe to state changes."""
        self._subscribers.append(callback)
    
    def _notify_subscribers(self) -> None:
        """Notify all subscribers of state change."""
        for callback in self._subscribers:
            try:
                callback(self.get_state())
            except Exception:
                pass  # Don't let subscriber errors affect main flow
    
    def get_state(self) -> AppState:
        """Get a copy of current state."""
        with self._lock:
            return AppState(
                status=self._state.status,
                instruction=self._state.instruction,
                plan=self._state.plan.copy(),
                current_step=self._state.current_step,
                next_step=self._state.next_step,
                reason=self._state.reason,
                events=self._state.events.copy(),
                screenshots=self._state.screenshots.copy(),
                question=self._state.question,
                question_id=self._state.question_id,
                result=self._state.result,
                error_message=self._state.error_message,
                started_at=self._state.started_at,
                completed_at=self._state.completed_at
            )
    
    def set_status(self, status: str) -> None:
        """Set the current status."""
        with self._lock:
            self._state.status = status
            if status == "done" and not self._state.completed_at:
                self._state.completed_at = datetime.utcnow().isoformat() + "Z"
        self._notify_subscribers()
    
    def set_instruction(self, instruction: str) -> None:
        """Set the user instruction."""
        with self._lock:
            self._state.instruction = instruction
        self._notify_subscribers()
    
    def set_plan(self, plan: List[str]) -> None:
        """Set the execution plan."""
        with self._lock:
            self._state.plan = plan.copy()
            if plan:
                self._state.next_step = plan[0]
        self._notify_subscribers()
    
    def set_current_step(self, step: str, reason: str = "") -> None:
        """Set the current step being executed."""
        with self._lock:
            self._state.current_step = step
            self._state.reason = reason
        self._notify_subscribers()
    
    def set_next_step(self, step: str) -> None:
        """Set the next planned step."""
        with self._lock:
            self._state.next_step = step
        self._notify_subscribers()
    
    def add_event(self, event_type: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Add an event to the timeline."""
        with self._lock:
            event = Event(
                timestamp=datetime.utcnow().isoformat() + "Z",
                event_type=event_type,
                message=message,
                details=details
            )
            self._state.events.append(event)
        self._notify_subscribers()
        return event
    
    def add_screenshot(self, filename: str, step: str, path: str) -> None:
        """Add a screenshot record."""
        with self._lock:
            screenshot = Screenshot(
                filename=filename,
                timestamp=datetime.utcnow().isoformat() + "Z",
                step=step,
                path=path
            )
            self._state.screenshots.append(screenshot)
        self._notify_subscribers()
        return screenshot
    
    def ask_question(self, question: str) -> int:
        """Ask the user a question and return question ID."""
        with self._lock:
            self._question_counter += 1
            self._state.question = question
            self._state.question_id = self._question_counter
            self._state.status = "paused"
        self._notify_subscribers()
        return self._state.question_id
    
    def answer_question(self, answer: str, question_id: int) -> bool:
        """Submit an answer to a question."""
        with self._lock:
            if self._state.question_id != question_id:
                return False
            self._state.question = None
            self._state.question_id = None
            self._state.status = "running"
            self.add_event("user_answer_received", f"User answered: {answer}")
        self._notify_subscribers()
        return True
    
    def set_result(self, result: str) -> None:
        """Set the final result."""
        with self._lock:
            self._state.result = result
            self._state.status = "done"
            if not self._state.completed_at:
                self._state.completed_at = datetime.utcnow().isoformat() + "Z"
        self._notify_subscribers()
    
    def set_error(self, error_message: str) -> None:
        """Set an error state."""
        with self._lock:
            self._state.error_message = error_message
            self._state.status = "error"
            self._state.completed_at = datetime.utcnow().isoformat() + "Z"
        self._notify_subscribers()
    
    def start_execution(self, instruction: str) -> None:
        """Mark execution as started."""
        with self._lock:
            self._state.status = "running"
            self._state.instruction = instruction
            self._state.started_at = datetime.utcnow().isoformat() + "Z"
            self._state.events.clear()
            self._state.screenshots.clear()
            self._state.result = None
            self._state.error_message = None
        self._notify_subscribers()
    
    def stop_execution(self) -> None:
        """Stop execution gracefully."""
        with self._lock:
            if self._state.status in ("running", "paused"):
                self._state.status = "stopped"
                self._state.completed_at = datetime.utcnow().isoformat() + "Z"
        self._notify_subscribers()
    
    def clear(self) -> None:
        """Clear all state."""
        with self._lock:
            self._state = AppState()
            self._question_counter = 0
        self._notify_subscribers()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for API responses."""
        with self._lock:
            return {
                "status": self._state.status,
                "instruction": self._state.instruction,
                "plan": self._state.plan,
                "current_step": self._state.current_step,
                "next_step": self._state.next_step,
                "reason": self._state.reason,
                "events": [e.to_dict() for e in self._state.events],
                "screenshots": [s.to_dict() for s in self._state.screenshots],
                "question": self._state.question,
                "question_id": self._state.question_id,
                "result": self._state.result,
                "error_message": self._state.error_message,
                "started_at": self._state.started_at,
                "completed_at": self._state.completed_at
            }
