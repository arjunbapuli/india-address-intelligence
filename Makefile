.PHONY: install run test lint eval ingest up

install:
	pip install -e ".[dev]"

run:            ## local API on SQLite, no Docker
	DATABASE_URL=sqlite:///./address_intel.db uvicorn app.main:app --reload

test:
	pytest

lint:
	ruff check app scripts tests evaluation

eval:
	python -m evaluation.run_eval --all

ingest:
	python -m scripts.ingest

up:
	docker compose up --build
