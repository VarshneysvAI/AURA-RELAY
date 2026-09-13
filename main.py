"""
AURA Relay - Main FastAPI Application
"""
import os
import json
import logging
import collections
import urllib.parse
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel

from aura.config import get_config, reload_config, Config
from aura.state import StateManager
from aura.email_provider import LocalEmailProvider, ImapEmailProvider, Email
from aura.security import SecurityManager
from aura.agent import AgentExecutor
from aura.anakin_client import get_anakin_client
from aura.llm_provider import get_llm_provider
from aura.tts_provider import get_tts_provider
from aura.query_cleaner import clean_search_query, build_search_url


# Backend in-memory logging capture
class InMemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self.buffer = collections.deque(maxlen=capacity)

    def emit(self, record):
        try:
            msg = self.format(record)
            # Filter out polling noise and static asset spam
            if any(p in msg for p in ["/api/state", "/api/events", "/api/logs", "/screenshots/", "/health", "favicon.ico"]):
                return
            clean_logger = record.name.replace("aura.", "").replace("uvicorn.", "")
            self.buffer.append({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": clean_logger,
                "message": msg,
            })
        except Exception:
            pass

log_handler = InMemoryLogHandler(capacity=500)
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
log_handler.setFormatter(log_formatter)

# Attach handler to root logger and key component loggers
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(log_handler)

for log_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "aura.llm", "aura.tts", "aura.anakin", "fastapi"]:
    l = logging.getLogger(log_name)
    l.addHandler(log_handler)

# Load configuration
config = get_config()

# Initialize components
state_manager = StateManager()
if config.email_provider == "imap" and config.imap_host and config.imap_user and config.imap_password:
    email_provider = ImapEmailProvider(
        host=config.imap_host,
        port=config.imap_port,
        user=config.imap_user,
        password=config.imap_password,
        folder=config.imap_folder
    )
else:
    email_provider = LocalEmailProvider()

security_manager = SecurityManager(redact_secrets=config.redact_secrets)
agent_executor: Optional[AgentExecutor] = None

# Runtime directory
RUNTIME_DIR = Path("runtime")
RUNTIME_DIR.mkdir(exist_ok=True)
AUDIO_DIR = RUNTIME_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)
EVENTS_FILE = RUNTIME_DIR / "events.jsonl"


class StartRequest(BaseModel):
    instruction: str


class AnswerRequest(BaseModel):
    answer: str


class OtpRequest(BaseModel):
    code: str


class ChatRequest(BaseModel):
    message: str
    gathered_info: Optional[dict] = None


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None


class SettingsUpdateRequest(BaseModel):
    anakin_api_key: Optional[str] = None
    nvidia_llm_api_key: Optional[str] = None
    nvidia_llm_model: Optional[str] = None
    groq_api_key: Optional[str] = None
    groq_model: Optional[str] = None
    nvidia_tts_api_key: Optional[str] = None
    nvidia_tts_voice: Optional[str] = None
    email_provider: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None
    headless: Optional[bool] = None


class BrowserClickRequest(BaseModel):
    x_ratio: float
    y_ratio: float


class BrowserTypeRequest(BaseModel):
    text: str
    press_enter: bool = False


class BrowserNavigateRequest(BaseModel):
    url: str


# Conversation history in memory
conversation_history = []

# Pending task context for upfront interactive Q&A
pending_task_context: Optional[dict] = None



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


async def _git_auto_sync_loop():
    """Periodically fetches latest commits from git origin/main and applies them automatically."""
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.sleep(25)
            proc = await asyncio.create_subprocess_exec(
                "git", "fetch", "origin", "main",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            if proc.returncode == 0:
                p_local = await asyncio.create_subprocess_exec(
                    "git", "rev-parse", "HEAD",
                    stdout=asyncio.subprocess.PIPE
                )
                stdout_l, _ = await p_local.communicate()
                p_remote = await asyncio.create_subprocess_exec(
                    "git", "rev-parse", "origin/main",
                    stdout=asyncio.subprocess.PIPE
                )
                stdout_r, _ = await p_remote.communicate()
                loc_h = stdout_l.strip()
                rem_h = stdout_r.strip()
                if loc_h and rem_h and loc_h != rem_h:
                    logger.info(f"New git commit detected ({rem_h.decode()[:7]})! Auto-syncing...")
                    p_pull = await asyncio.create_subprocess_exec(
                        "git", "reset", "--hard", "origin/main",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await p_pull.communicate()
                    state_manager.add_event("git_sync", f"Auto-synced repository to {rem_h.decode()[:7]}")
        except Exception:
            await asyncio.sleep(25)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler replacing deprecated startup/shutdown events."""
    # Startup: Clear events file & suppress Windows Proactor connection lost noise
    EVENTS_FILE.unlink(missing_ok=True)
    try:
        current_loop = asyncio.get_running_loop()
        def _proactor_handler(lp, ctx):
            exc = ctx.get("exception")
            if isinstance(exc, ConnectionResetError) or (isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054):
                return
            lp.default_exception_handler(ctx)
        current_loop.set_exception_handler(_proactor_handler)
    except Exception:
        pass
    sync_task = asyncio.create_task(_git_auto_sync_loop())
    yield
    sync_task.cancel()
    # Shutdown: Graceful browser cleanup
    agent = get_agent()
    if agent and hasattr(agent, 'browser') and agent.browser:
        try:
            if hasattr(agent, 'loop') and agent.loop:
                future = asyncio.run_coroutine_threadsafe(
                    agent.browser.close(keep_session=True),
                    agent.loop
                )
                try:
                    future.result(timeout=3)
                except Exception:
                    pass
            if hasattr(agent, 'loop') and agent.loop:
                agent.loop.call_soon_threadsafe(agent.loop.stop)
        except Exception:
            pass



# Initialize application with modern lifespan handler
app = FastAPI(title="AURA Relay", description="Ask. Verify. Act.", lifespan=lifespan)


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


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """Conversational coworker endpoint combining LLM reasoning, Anakin web intelligence, and TTS voice."""
    global pending_task_context
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="Empty message")
        
    conversation_history.append({"role": "user", "content": user_msg})
    llm = get_llm_provider()
    anakin = get_anakin_client()
    tts = get_tts_provider()
    agent = get_agent()

    # 0. If agent is currently running and waiting for user input, route message directly as the answer!
    if agent.is_running() and (state_manager.get_state().status == "waiting_for_user" or (hasattr(agent, "_question_resolved") and not agent._question_resolved.is_set())):
        agent.submit_answer(user_msg)
        coworker_msg = f"Got it! Continuing with your choice: '{user_msg}'"
        audio_url, _ = await tts.synthesize(coworker_msg)
        conversation_history.append({"role": "assistant", "content": coworker_msg})
        return {
            "type": "chat_reply",
            "reply": coworker_msg,
            "audio_url": audio_url,
            "action": "answering"
        }

    # 1. If we were waiting for an answer to an upfront question, this message is the user's answer!
    if pending_task_context:
        orig_goal = pending_task_context.get("goal", user_msg)
        pending_task_context = None

        combined_instruction = f"{orig_goal} (User preference: {user_msg})"
        clean_info = await llm.optimize_search_query(combined_instruction, conversation_history)
        clean_query = clean_info["keywords"]
        platform = clean_info["platform"]
        
        # Pre-flight Anakin search
        direct_url = None
        web_intel = None
        if platform != "portal":
            s_res = await anakin.search(clean_query, limit=3)
            if s_res and "results" in s_res and s_res["results"]:
                results = s_res["results"]
                web_intel = "\n".join([f"- {r.get('title', '')}: {r.get('snippet', '')[:140]}" for r in results[:3]])
                if results[0].get("url", "").startswith("http"):
                    direct_url = results[0]["url"]

        plan = await llm.plan_task(combined_instruction, conversation_history=conversation_history, web_intelligence=web_intel)
        clean_query = plan.get("optimized_search_query") or clean_query

        if direct_url and platform not in ["ebay", "amazon", "wikipedia", "youtube"]:
            target_url = direct_url
        else:
            target_url = build_search_url(platform, clean_query, clean_info.get("max_price"))

        coworker_msg = plan.get("coworker_message") or f"Got it! Starting search for {clean_query} on {platform.capitalize()}."
        audio_url, _ = await tts.synthesize(coworker_msg)

        if agent.is_running():
            agent.stop()
            await asyncio.sleep(0.3)
        agent.start(combined_instruction, {
            "clean_keywords": clean_query,
            "optimized_query": clean_query,
            "max_price": clean_info["max_price"],
            "target_url": target_url,
            "user_preference": user_msg,
            "web_intelligence": web_intel,
            **(req.gathered_info or {})
        })
        conversation_history.append({"role": "assistant", "content": coworker_msg})
        return {
            "type": "task_started",
            "reply": coworker_msg,
            "audio_url": audio_url,
            "action": "executing"
        }

    # 2. Extract clean search parameters via fast heuristic cleaner
    clean_info = await llm.optimize_search_query(user_msg, conversation_history)
    clean_query = clean_info["keywords"]
    platform = clean_info["platform"]

    # Pre-flight search with Anakin.io web intelligence (1 API call)
    web_intel = None
    direct_url = None
    if platform != "portal":
        s_res = await anakin.search(clean_query, limit=3)
        if s_res and "results" in s_res and s_res["results"]:
            results = s_res["results"]
            web_intel = "\n".join([f"- {r.get('title', '')}: {r.get('snippet', '')[:140]}" for r in results[:3]])
            if results[0].get("url", "").startswith("http"):
                direct_url = results[0]["url"]
            
    # Upfront coworker planning with conversational memory (SINGLE LLM CALL)
    plan = await llm.plan_task(user_msg, conversation_history=conversation_history, web_intelligence=web_intel)
    clean_query = plan.get("optimized_search_query") or clean_query
    coworker_msg = plan.get("coworker_message") or f"I am ready to help you with: {user_msg}"
    needs_info = plan.get("needs_upfront_info", False)
    questions = plan.get("questions", [])
    if questions:
        # Strictly ask at most ONE question at a time
        questions = [questions[0]]
    
    # Synthesize spoken voice (returns None on API limit, handled gracefully by frontend)
    audio_url, _ = await tts.synthesize(coworker_msg)
    
    # If the user is having a conversation or asking about capabilities:
    is_inquiry = plan.get("task_type") == "inquiry" or any(
        phrase in user_msg.lower()
        for phrase in ["describe your work", "how do you work", "what can you do", "who are you", "help me understand"]
    )
    if is_inquiry:
        conversation_history.append({"role": "assistant", "content": coworker_msg})
        return {
            "type": "chat_reply",
            "reply": coworker_msg,
            "plan": plan,
            "audio_url": audio_url,
            "action": "none"
        }
    
    # If the task requires credentials/preferences not yet provided:
    if needs_info and questions and not req.gathered_info:
        pending_task_context = {
            "goal": user_msg,
            "question": questions[0]
        }
        conversation_history.append({"role": "assistant", "content": coworker_msg})
        return {
            "type": "ask_upfront",
            "reply": coworker_msg,
            "questions": questions,
            "plan": plan,
            "audio_url": audio_url,
            "action": "wait_input"
        }
    
    # Determine destination URL
    if direct_url and platform not in ["ebay", "amazon", "wikipedia", "youtube"]:
        target_url = direct_url
    else:
        target_url = build_search_url(platform, clean_query, clean_info.get("max_price"))

    # Trigger agent execution
    if agent.is_running():
        agent.stop()
        await asyncio.sleep(0.3)
    agent.start(user_msg, {
        "clean_keywords": clean_query,
        "optimized_query": clean_query,
        "max_price": clean_info["max_price"],
        "target_url": target_url,
        "web_intelligence": web_intel,
        **(req.gathered_info or {})
    })
        
    conversation_history.append({"role": "assistant", "content": coworker_msg})
    return {
        "type": "task_started",
        "reply": coworker_msg,
        "plan": plan,
        "audio_url": audio_url,
        "action": "executing"
    }


@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest):
    """Synthesize text to speech on demand with RealtimeTTS support."""
    from aura.tts_provider import play_realtime_tts, get_tts_provider
    play_realtime_tts(req.text)
    tts = get_tts_provider()
    audio_url, is_cached = await tts.synthesize(req.text, req.voice)
    return {"audio_url": audio_url, "cached": is_cached}


@app.post("/api/tts/stop")
async def tts_stop():
    """Immediately stop speech playback on user interruption."""
    from aura.tts_provider import stop_realtime_tts
    stopped = stop_realtime_tts()
    return {"status": "stopped", "interrupted": stopped}


@app.get("/api/audio/{filename}")
async def serve_audio_endpoint(filename: str):
    """Serve cached TTS audio file."""
    file_path = AUDIO_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(str(file_path), media_type="audio/wav")


@app.get("/api/chat/history")
async def get_chat_history():
    """Retrieve chat history."""
    return {"history": conversation_history}


@app.post("/api/chat/clear")
async def clear_chat_history():
    """Clear conversation history and reset state."""
    global conversation_history, pending_task_context
    conversation_history = []
    pending_task_context = None
    agent = get_agent()
    if agent.is_running():
        agent.stop()
    state_manager.reset()
    try:
        # Prevent File I/O leak by deleting old events
        EVENTS_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return {"status": "cleared"}


@app.get("/api/settings")
async def get_settings():
    """Get current configuration with masked secrets for dashboard display."""
    cfg = get_config()
    def mask(val: Optional[str]) -> str:
        if not val:
            return ""
        if len(val) <= 8:
            return "********"
        return val[:6] + "..." + val[-4:]
        
    return {
        "anakin_api_key_masked": mask(cfg.anakin_api_key),
        "anakin_configured": bool(cfg.anakin_api_key),
        "nvidia_llm_api_key_masked": mask(cfg.nvidia_llm_api_key),
        "nvidia_llm_configured": bool(cfg.nvidia_llm_api_key),
        "nvidia_llm_model": cfg.nvidia_llm_model,
        "groq_api_key_masked": mask(cfg.groq_api_key),
        "groq_configured": bool(cfg.groq_api_key),
        "groq_model": cfg.groq_model,
        "nvidia_tts_api_key_masked": mask(cfg.nvidia_tts_api_key),
        "nvidia_tts_configured": bool(cfg.nvidia_tts_api_key),
        "nvidia_tts_voice": cfg.nvidia_tts_voice,
        "email_provider": cfg.email_provider,
        "imap_host": cfg.imap_host or "",
        "imap_port": cfg.imap_port,
        "imap_user": cfg.imap_user or "",
        "headless": cfg.headless
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    """Update settings dynamically from the dashboard and persist to .env."""
    env_updates = {}
    if req.anakin_api_key is not None and req.anakin_api_key.strip():
        env_updates["ANAKIN_API_KEY"] = req.anakin_api_key.strip()
    if req.nvidia_llm_api_key is not None and req.nvidia_llm_api_key.strip():
        env_updates["NVIDIA_LLM_API_KEY"] = req.nvidia_llm_api_key.strip()
    if req.nvidia_llm_model is not None and req.nvidia_llm_model.strip():
        env_updates["NVIDIA_LLM_MODEL"] = req.nvidia_llm_model.strip()
    if req.groq_api_key is not None and req.groq_api_key.strip():
        env_updates["GROQ_API_KEY"] = req.groq_api_key.strip()
    if req.groq_model is not None and req.groq_model.strip():
        env_updates["GROQ_MODEL"] = req.groq_model.strip()
    if req.nvidia_tts_api_key is not None and req.nvidia_tts_api_key.strip():
        env_updates["NVIDIA_TTS_API_KEY"] = req.nvidia_tts_api_key.strip()
    if req.nvidia_tts_voice is not None and req.nvidia_tts_voice.strip():
        env_updates["NVIDIA_TTS_VOICE"] = req.nvidia_tts_voice.strip()
    if req.email_provider is not None:
        env_updates["EMAIL_PROVIDER"] = req.email_provider
    if req.imap_host is not None:
        env_updates["IMAP_HOST"] = req.imap_host
    if req.imap_port is not None:
        env_updates["IMAP_PORT"] = str(req.imap_port)
    if req.imap_user is not None:
        env_updates["IMAP_USER"] = req.imap_user
    if req.imap_password is not None and req.imap_password.strip():
        env_updates["IMAP_PASSWORD"] = req.imap_password.strip()
    if req.headless is not None:
        env_updates["HEADLESS"] = "true" if req.headless else "false"

    # Update os.environ
    for k, v in env_updates.items():
        os.environ[k] = v

    # Persist to .env file
    env_path = Path(".env")
    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()

    updated_keys = set(env_updates.keys())
    new_lines = []
    for line in existing_lines:
        matched = False
        for k in updated_keys:
            if line.startswith(f"{k}="):
                new_lines.append(f"{k}={env_updates[k]}")
                updated_keys.remove(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for k in updated_keys:
        new_lines.append(f"{k}={env_updates[k]}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    global config, agent_executor, email_provider
    if agent_executor is not None:
        try:
            if hasattr(agent_executor, 'browser') and agent_executor.browser is not None:
                await agent_executor.browser.close()
            if hasattr(agent_executor, 'playwright') and agent_executor.playwright is not None:
                await agent_executor.playwright.stop()
        except Exception as e:
            logger.warning(f"Error closing previous browser on settings update: {e}")
    config = reload_config()
    agent_executor = None
    import aura.anakin_client as ac
    import aura.llm_provider as lp
    import aura.tts_provider as tp
    ac._anakin_client = None
    lp._llm_provider = None
    tp._tts_provider = None
    if config.email_provider == "imap" and config.imap_host and config.imap_user and config.imap_password:
        email_provider = ImapEmailProvider(
            host=config.imap_host,
            port=config.imap_port,
            user=config.imap_user,
            password=config.imap_password,
            folder=config.imap_folder
        )
    else:
        email_provider = LocalEmailProvider()
    return {"status": "updated", "updated_fields": list(env_updates.keys())}


@app.get("/api/logs")
async def get_logs(limit: int = 100):
    """Retrieve recent backend server logs."""
    all_logs = list(log_handler.buffer)
    return {"logs": all_logs[-limit:], "total": len(all_logs)}


@app.post("/api/logs/clear")
async def clear_logs():
    """Clear backend server logs buffer."""
    log_handler.buffer.clear()
    return {"status": "cleared"}


@app.post("/api/git/sync")
async def trigger_git_sync():
    """Manual trigger to fetch and apply latest changes from GitHub."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin", "main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        p_pull = await asyncio.create_subprocess_exec(
            "git", "reset", "--hard", "origin/main",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_p, stderr_p = await p_pull.communicate()
        state_manager.add_event("git_sync", "Manually synced latest repository changes!")
        return {"status": "success", "detail": stdout_p.decode()}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/git/status")
async def get_git_status():
    """Check git repository commit hash and branch."""
    try:
        p_local = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--short", "HEAD",
            stdout=asyncio.subprocess.PIPE
        )
        stdout_l, _ = await p_local.communicate()
        p_msg = await asyncio.create_subprocess_exec(
            "git", "log", "-1", "--pretty=%B",
            stdout=asyncio.subprocess.PIPE
        )
        stdout_m, _ = await p_msg.communicate()
        return {
            "status": "ok",
            "commit": stdout_l.decode().strip(),
            "message": stdout_m.decode().strip()
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}



@app.post("/api/start")
async def start_agent(request: StartRequest):
    """Start the agent with given instruction."""
    import sys
    print(f"DEBUG: agent module = {sys.modules['aura.agent'].__file__}")
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


@app.post("/api/pause")
async def pause_agent():
    """Pause the running agent."""
    agent = get_agent()
    if not agent.is_running():
        raise HTTPException(status_code=400, detail="Agent not running")
    agent.pause()
    return {"status": "paused"}


@app.post("/api/resume")
async def resume_agent():
    """Resume the paused agent."""
    agent = get_agent()
    if not agent.is_running():
        raise HTTPException(status_code=400, detail="Agent not running")
    agent.resume()
    return {"status": "resumed"}


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


@app.post("/api/browser/click")
async def browser_click(req: BrowserClickRequest):
    """Directly click on the browser page at the given relative coordinates."""
    agent = get_agent()
    if not hasattr(agent, "browser") or not agent.browser:
        raise HTTPException(status_code=400, detail="Browser not available")
    
    try:
        await agent.browser.ensure_launched()
        page = agent.browser.page
        if not page:
            raise HTTPException(status_code=400, detail="Browser page not available")
            
        viewport = page.viewport_size or {"width": 1280, "height": 800}
        if not page.viewport_size:
            try:
                dim = await page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
                if dim and dim.get("width"):
                    viewport = dim
            except Exception:
                pass

        click_x = max(0, min(viewport["width"], req.x_ratio * viewport["width"]))
        click_y = max(0, min(viewport["height"], req.y_ratio * viewport["height"]))

        await page.mouse.click(click_x, click_y)
        await asyncio.sleep(0.5)

        filename = await agent.browser.screenshot("user_click.png")
        if filename:
            state_manager.add_screenshot(filename, "User Viewport Click", f"/screenshots/{filename}")
        return {
            "status": "clicked",
            "screenshot": f"/screenshots/{filename}" if filename else None,
            "x": click_x,
            "y": click_y,
            "url": page.url,
            "title": await agent.browser.get_title()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/browser/type")
async def browser_type(req: BrowserTypeRequest):
    """Directly type text into the active/focused browser element."""
    agent = get_agent()
    if not hasattr(agent, "browser") or not agent.browser:
        raise HTTPException(status_code=400, detail="Browser not available")
    
    try:
        await agent.browser.ensure_launched()
        page = agent.browser.page
        if not page:
            raise HTTPException(status_code=400, detail="Browser page not available")

        if req.text:
            await page.keyboard.type(req.text)
        if req.press_enter:
            await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)

        filename = await agent.browser.screenshot("user_type.png")
        if filename:
            state_manager.add_screenshot(filename, "User Viewport Type", f"/screenshots/{filename}")
        return {
            "status": "typed",
            "screenshot": f"/screenshots/{filename}" if filename else None,
            "url": page.url,
            "title": await agent.browser.get_title()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/browser/navigate")
async def browser_navigate(req: BrowserNavigateRequest):
    """Directly navigate the browser to a given URL."""
    agent = get_agent()
    if not hasattr(agent, "browser") or not agent.browser:
        raise HTTPException(status_code=400, detail="Browser not available")
    
    try:
        await agent.browser.ensure_launched()
        await agent.browser.navigate(req.url)
        filename = await agent.browser.screenshot("user_nav.png")
        if filename:
            state_manager.add_screenshot(filename, "User Viewport Nav", f"/screenshots/{filename}")
        return {
            "status": "navigated",
            "screenshot": f"/screenshots/{filename}" if filename else None,
            "url": agent.browser.page.url if agent.browser.page else req.url,
            "title": await agent.browser.get_title() if agent.browser.page else ""
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/answer")
async def submit_answer(request: AnswerRequest):
    """Submit user answer to agent question."""
    agent = get_agent()
    state = state_manager.to_dict()
    
    question_id = state.get("question_id")
    if question_id is not None:
        agent.submit_answer(request.answer, question_id)
        return {"status": "answered", "answer": request.answer}
    elif agent.is_running() and hasattr(agent, "_question_resolved") and not agent._question_resolved.is_set():
        agent._last_user_answer = request.answer
        agent._question_resolved.set()
        return {"status": "answered", "answer": request.answer}
    else:
        raise HTTPException(status_code=400, detail="No pending question to answer")


@app.get("/api/events")
async def stream_events() -> StreamingResponse:
    """Server-sent events stream for real-time updates."""
    
    async def event_generator() -> AsyncGenerator[str, None]:
        last_event_count = 0
        last_screenshot_count = 0
        last_status = None
        
        while True:
            state = state_manager.to_dict()
            events = state.get("events", [])
            screenshots = state.get("screenshots", [])
            current_status = state.get("status")
            
            state_snapshot = {
                "status": state["status"],
                "current_step": state["current_step"],
                "next_step": state["next_step"],
                "reason": state["reason"],
                "question": state["question"],
                "question_id": state["question_id"],
                "result": state.get("result"),
                "error": state.get("error"),
                "screenshots": screenshots,
            }
            
            if len(events) > last_event_count:
                new_events = events[last_event_count:]
                for event in new_events:
                    event["state"] = state_snapshot
                    yield f"data: {json.dumps(event)}\n\n"
                    persist_event(event)
                
                last_event_count = len(events)
                last_screenshot_count = len(screenshots)
                last_status = current_status
            elif len(screenshots) > last_screenshot_count or current_status != last_status:
                sync_event = {
                    "type": "viewport_sync",
                    "message": "Live browser viewport updated",
                    "state": state_snapshot
                }
                yield f"data: {json.dumps(sync_event)}\n\n"
                last_screenshot_count = len(screenshots)
                last_status = current_status
            
            await asyncio.sleep(0.15)
    
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
    
    return FileResponse(
        str(filepath),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"}
    )


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
        timestamp=datetime.now(timezone.utc).isoformat(),
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
