.PHONY: install lint test run demo prod logs

install:
	pip install -r requirements.txt -r requirements-dev.txt

lint:
	ruff check app tests scripts

test:
	pytest -q

run:
	uvicorn app.main:app --reload --port 8000

demo:
	python scripts/demo.py

prod:
	docker compose up --build -d

logs:
	docker compose logs -f api
