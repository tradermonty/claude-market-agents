.PHONY: lint format test typecheck security audit spell all install golden

lint:
	ruff check . && ruff format --check .

format:
	ruff check --fix . && ruff format .

test:
	pytest backtest/tests/ live/tests/ -v --cov=backtest --cov=live --cov-report=term-missing --cov-fail-under=70

typecheck:
	mypy backtest/ live/ --config-file=pyproject.toml

security:
	bandit -c pyproject.toml -r backtest/ live/

audit:
	pip-audit --desc on

spell:
	codespell backtest/ live/

golden:
	python -m backtest.tests.generate_golden

all: lint test typecheck security audit spell

install:
	pip install -e ".[dev]"
	pre-commit install
