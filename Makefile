.PHONY: lint format test typecheck security all install golden

lint:
	ruff check . && ruff format --check .

format:
	ruff check --fix . && ruff format .

test:
	pytest backtest/tests/ -v --cov=backtest --cov-report=term-missing --cov-fail-under=60

typecheck:
	mypy backtest/ --config-file=pyproject.toml

security:
	bandit -r backtest/ -x backtest/tests/ --severity-level medium

golden:
	python -m backtest.tests.generate_golden

all: lint test typecheck

install:
	pip install -e ".[dev]"
	pre-commit install
