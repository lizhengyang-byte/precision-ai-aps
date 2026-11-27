.PHONY: install dev test demo docker

install:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

demo:
	python scripts/demo.py --plot

docker:
	docker build -t precision-ai-aps . && docker run -p 8000:8000 precision-ai-aps
