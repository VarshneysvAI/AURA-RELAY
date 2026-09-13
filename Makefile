# AURA Relay Makefile

.PHONY: install playwright run test smoke docker-build docker-run clean dev

install:
	pip install -r requirements.txt

playwright:
	python -m playwright install chromium

run:
	python main.py

test:
	pytest -q

smoke:
	python scripts/smoke.py

docker-build:
	docker build -t aura-relay .

docker-run:
	docker-compose up

dev:
	bash scripts/dev.sh

clean:
	rm -rf __pycache__ .pytest_cache runtime/screenshots/* runtime/events.jsonl
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
