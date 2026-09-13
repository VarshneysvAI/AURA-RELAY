# AURA Relay Runbook

## Quick Reference

### Start the Application
```bash
# Development mode
python main.py

# Or using Makefile
make run

# In Docker
docker-compose up
```

### Install Dependencies
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### Run Tests
```bash
# Unit tests
pytest -q

# Smoke test (server must be running)
python scripts/smoke.py
```

## Environment Variables

Copy `.env.example` to `.env` and edit:

| Variable | Default | Description |
|----------|---------|-------------|
| `MODE` | sandbox | `sandbox` or `live` |
| `APP_HOST` | 127.0.0.1 | Server host |
| `APP_PORT` | 8000 | Server port |
| `HEADLESS` | false | Browser headless mode |
| `SLOW_MO` | 250 | Browser slow motion (ms) |
| `TARGET_URL` | - | Live target website |
| `TARGET_USERNAME` | - | Live credentials |
| `TARGET_PASSWORD` | - | Live credentials |
| `EMAIL_PROVIDER` | local | `local`, `imap`, or `http` |

## Debugging

### Check Server Health
```bash
curl http://127.0.0.1:8000/health
```

### View Current State
```bash
curl http://127.0.0.1:8000/api/state
```

### View Logs
- Events are logged to `runtime/events.jsonl`
- Screenshots saved to `runtime/screenshots/`

### Common Errors

**Browser launch failed:**
```
Error: Executable doesn't exist at...
```
Solution: Run `python -m playwright install chromium`

**Port already in use:**
```
Address already in use
```
Solution: Change `APP_PORT` in `.env` or kill process on port 8000

**Headful browser fails in container:**
Solution: Set `HEADLESS=true` in environment

**OTP not found:**
- Check that login triggered OTP email
- Verify email contains numeric code
- Check `/api/inbox` endpoint

## Adding New Tasks

1. Create task definition in `tasks/` directory
2. Add plan to `aura/planner.py`
3. Implement flow in `aura/agent.py`

## Switching Email Providers

### IMAP (Gmail example)
```env
EMAIL_PROVIDER=imap
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=you@gmail.com
IMAP_PASSWORD=your-app-password
```

### HTTP API
```env
EMAIL_PROVIDER=http
EMAIL_HTTP_URL=https://api.emailservice.com
EMAIL_HTTP_API_KEY=your-api-key
```

## Docker Operations

```bash
# Build image
docker build -t aura-relay .

# Run container
docker run -p 8000:8000 aura-relay

# Using docker-compose
docker-compose up -d
docker-compose logs -f
```

## Production Deployment

1. Set `MODE=live`
2. Configure `TARGET_URL` and credentials
3. Set up proper email provider
4. Use environment variables for secrets
5. Consider adding authentication layer
6. Enable HTTPS for voice features

## Monitoring

- Health endpoint: `/health`
- State polling: `/api/state`
- Event stream: `/api/events` (SSE)
- Screenshot directory: `runtime/screenshots/`

## Security Notes

- Passwords masked in UI by default
- OTP codes masked (last 2 digits shown)
- Secrets not written to event logs
- `REDACT_SECRETS=true` by default

---

For more information, see README.md
