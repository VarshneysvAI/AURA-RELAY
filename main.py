"""
AURA Relay - Main FastAPI Application
"""
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

from aura.config import get_config, Config
from aura.state import StateManager
from aura.email_provider import LocalEmailProvider, Email
from aura.security import SecurityManager
from aura.agent import AgentExecutor


# Initialize application
app = FastAPI(title="AURA Relay", description="Ask. Verify. Act.")

# Load configuration
config = get_config()

# Initialize components
state_manager = StateManager()
email_provider = LocalEmailProvider()
security_manager = SecurityManager(redact_secrets=config.redact_secrets)
agent_executor: Optional[AgentExecutor] = None

# Runtime directory
RUNTIME_DIR = Path("runtime")
RUNTIME_DIR.mkdir(exist_ok=True)
EVENTS_FILE = RUNTIME_DIR / "events.jsonl"


class StartRequest(BaseModel):
    instruction: str


class AnswerRequest(BaseModel):
    answer: str


class OtpRequest(BaseModel):
    code: str


def get_agent() -> AgentExecutor:
    """Get or create agent executor."""
    global agent_executor
    if agent_executor is None:
        agent_executor = AgentExecutor(
            state_manager=state_manager,
            email_provider=email_provider,
            config=config,
            security_manager=security_manager
        )
    return agent_executor


def persist_event(event_data: dict) -> None:
    """Persist event to JSONL file."""
    try:
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(event_data) + "\n")
    except Exception:
        pass


@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    # Clear events file
    EVENTS_FILE.unlink(missing_ok=True)


@app.get("/")
async def root():
    """Serve the main UI."""
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "mode": config.mode,
        "version": "1.0.0"
    }


@app.get("/api/state")
async def get_state():
    """Get current application state."""
    return state_manager.to_dict()


@app.post("/api/start")
async def start_agent(request: StartRequest):
    """Start the agent with given instruction."""
    agent = get_agent()
    
    if agent.is_running():
        raise HTTPException(status_code=400, detail="Agent already running")
    
    # Register credentials for redaction
    security_manager.register_secret("password", "testpass123")
    
    # Start agent
    agent.start(request.instruction)
    
    return {"status": "started", "instruction": request.instruction}


@app.post("/api/stop")
async def stop_agent():
    """Stop the running agent."""
    agent = get_agent()
    
    if not agent.is_running():
        raise HTTPException(status_code=400, detail="Agent not running")
    
    agent.stop()
    state_manager.stop_execution()
    
    return {"status": "stopped"}


@app.post("/api/retry")
async def retry_step():
    """Retry the current failed step."""
    # For now, just restart with same instruction
    state = state_manager.to_dict()
    instruction = state.get("instruction", "")
    
    if not instruction:
        raise HTTPException(status_code=400, detail="No instruction to retry")
    
    agent = get_agent()
    if agent.is_running():
        raise HTTPException(status_code=400, detail="Agent still running")
    
    agent.start(instruction)
    return {"status": "retrying"}


@app.post("/api/answer")
async def submit_answer(request: AnswerRequest):
    """Submit user answer to agent question."""
    agent = get_agent()
    state = state_manager.to_dict()
    
    question_id = state.get("question_id")
    if question_id is None:
        raise HTTPException(status_code=400, detail="No pending question")
    
    success = agent.submit_answer(request.answer, question_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    return {"status": "answered", "answer": request.answer}


@app.get("/api/events")
async def stream_events() -> StreamingResponse:
    """Server-sent events stream for real-time updates."""
    
    async def event_generator() -> AsyncGenerator[str, None]:
        last_event_count = 0
        
        while True:
            state = state_manager.to_dict()
            events = state.get("events", [])
            
            if len(events) > last_event_count:
                new_events = events[last_event_count:]
                for event in new_events:
                    event["state"] = {
                        "status": state["status"],
                        "current_step": state["current_step"],
                        "next_step": state["next_step"],
                        "reason": state["reason"],
                        "question": state["question"],
                        "question_id": state["question_id"]
                    }
                    yield f"data: {json.dumps(event)}\n\n"
                    persist_event(event)
                
                last_event_count = len(events)
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/api/screenshots")
async def list_screenshots():
    """List all screenshots."""
    screenshot_dir = Path("runtime/screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    
    screenshots = []
    for f in sorted(screenshot_dir.glob("*.png")):
        screenshots.append({
            "filename": f.name,
            "path": f"/screenshots/{f.name}",
            "size": f.stat().st_size
        })
    
    return screenshots


@app.get("/screenshots/{filename}")
async def serve_screenshot(filename: str):
    """Serve a screenshot file."""
    filepath = Path("runtime/screenshots") / filename
    
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    
    return FileResponse(str(filepath), media_type="image/png")


@app.get("/api/inbox")
async def get_inbox():
    """Get email inbox (sandbox mode)."""
    emails = email_provider.get_inbox()
    return {"emails": [e.to_dict() for e in emails]}


@app.post("/api/sandbox/send-otp")
async def sandbox_send_otp():
    """Sandbox: Simulate sending OTP email."""
    otp_code = "123456"  # Fixed test OTP
    
    email = Email(
        sender="secure-portal@example.com",
        subject="Your Verification Code",
        body=f"Your one-time password (OTP) is: {otp_code}. This code will expire in 10 minutes.",
        timestamp=datetime.utcnow().isoformat() + "Z",
        otp_code=otp_code
    )
    
    email_provider.add_email(email)
    
    return {"status": "sent", "message": "OTP email sent to inbox"}


@app.post("/api/sandbox/verify-otp")
async def sandbox_verify_otp(request: OtpRequest):
    """Sandbox: Verify OTP code."""
    expected_otp = email_provider.get_latest_otp()
    
    if request.code == expected_otp or request.code == "123456":
        return {"status": "verified", "message": "OTP verified successfully"}
    else:
        raise HTTPException(status_code=400, detail="Invalid OTP code")


# Mount static files
app.mount("/sandbox", StaticFiles(directory="sandbox"), name="sandbox")
app.mount("/", StaticFiles(directory="static", html=True), name="static")


# Import asyncio for event generator
import asyncio


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        app,
        host=config.app_host,
        port=config.app_port
    )
