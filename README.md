# AURA Relay

**Tagline:** Ask. Verify. Act.

A transparent, voice-assisted, self-healing web agent that can receive instructions, show its plan, execute browser automation with full transparency, handle OTP flows, and ask for human help when needed.

## Features

- **Real Browser Automation**: Uses Playwright to control a real Chromium browser
- **Full Transparency**: Every action, thought, screenshot, and result is visible in the UI
- **Self-Healing DOM Intelligence**: Handles changing button text, dynamic classes, and unstable selectors
- **OTP Flow Support**: Reads emails, extracts OTP codes, and fills them automatically
- **Human-in-the-Loop**: Asks for help when stuck or when choices are ambiguous
- **Voice Support**: Web Speech API for voice input/output (where supported)
- **Sandbox Mode**: Built-in local web application for verified end-to-end execution
- **Live Mode Ready**: Switch to real target URLs and email APIs via environment variables

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright Chromium
python -m playwright install chromium

# 3. Run the application
python main.py

# 4. Open browser to http://127.0.0.1:8000
```

Or use the Makefile:
```bash
make install
make playwright
make run
```

## Project Structure

```
aura-relay/
├── main.py                 # FastAPI application
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
├── Makefile               # Build commands
├── Dockerfile             # Docker build
├── docker-compose.yml     # Docker orchestration
│
├── aura/                  # Core Python package
│   ├── __init__.py
│   ├── config.py          # Configuration management
│   ├── state.py           # Thread-safe state manager
│   ├── events.py          # Event system
│   ├── security.py        # Secret redaction
│   ├── browser_manager.py # Playwright wrapper
│   ├── dom_intelligence.py# Smart element location
│   ├── email_provider.py  # Email interface + implementations
│   ├── otp.py             # OTP extraction utilities
│   ├── planner.py         # Task planning
│   ├── agent.py           # Main agent executor
│   └── api.py             # API routes
│
├── static/                # Frontend files
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── logo.svg
│
├── sandbox/               # Sandbox web portal
│   ├── login.html
│   ├── otp.html
│   ├── mail.html
│   └── dashboard.html
│
├── tasks/                 # Task definitions
│   └── secure_portal_document_retrieval.json
│
├── runtime/               # Runtime data
│   ├── events.jsonl
│   └── screenshots/
│
├── scripts/
│   ├── smoke.py           # Smoke test
│   └── dev.sh             # Development script
│
└── tests/
    ├── test_smoke.py      # Unit tests
    └── ...
```

## How Transparency Works

The UI shows in real-time:
1. **Current Status**: idle, running, paused, done, error
2. **Current Step**: What the agent is doing now
3. **Next Step**: What's planned next
4. **Agent Reason**: Why the agent is taking this action
5. **Timeline**: All events with timestamps
6. **Screenshots**: Evidence after each major action
7. **Email Inbox**: OTP emails received
8. **Questions**: When the agent needs human help

## Switching to Live Mode

Edit `.env` (copy from `.env.example`):

```bash
# Switch to live mode
MODE=live

# Set your target website
TARGET_URL=https://your-portal.com
TARGET_USERNAME=your-username
TARGET_PASSWORD=your-password

# Configure email provider
EMAIL_PROVIDER=imap  # or 'http' for API-based
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=your-email@gmail.com
IMAP_PASSWORD=your-app-password
```

## Running Tests

```bash
# Unit tests
pytest -q

# Smoke test (requires server running)
python scripts/smoke.py
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main UI |
| `/health` | GET | Health check |
| `/api/state` | GET | Current state |
| `/api/start` | POST | Start agent |
| `/api/stop` | POST | Stop agent |
| `/api/retry` | POST | Retry failed step |
| `/api/answer` | POST | Submit user answer |
| `/api/events` | GET | SSE stream |
| `/api/screenshots` | GET | List screenshots |
| `/api/inbox` | GET | Email inbox |

## Troubleshooting

### Browser won't launch
- Ensure Playwright Chromium is installed: `python -m playwright install chromium`
- In headless environments, set `HEADLESS=true`
- Check for missing system dependencies

### Port already in use
- Change `APP_PORT` in `.env`
- Or kill the process on port 8000

### OTP not detected
- Check email inbox at `/sandbox/mail.html`
- Ensure email contains a 4-8 digit code
- OTP must be in email body

### Voice not working
- Web Speech API requires Chrome/Edge
- Some browsers need HTTPS for speech recognition
- Text input always works as fallback

## Future API Integration Points

### Email Providers
- `LocalEmailProvider`: Default sandbox mode
- `ImapEmailProvider`: For IMAP email access
- `HttpEmailProvider`: For REST API email services
- Future: Gmail API, Outlook API

### Target Sites
- Currently uses sandbox portal
- Set `TARGET_URL` for live sites
- Add custom adapters in `dom_intelligence.py`

### LLM Integration
- Currently uses deterministic planning
- Add `LLMAdapter` for smarter reasoning
- Future: OpenAI API integration

### Anti-Bot Handling
- Detects captchas and security walls
- Pauses and asks user for help
- Future: Anakin API adapter

## License

MIT License

---

**AURA Relay v1.0.0** | Transparent AI Agent
