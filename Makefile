PACKAGE := src

.PHONY: start
start:
	uv run python -m $(PACKAGE).main

.PHONY: lint
lint:
	ruff check $(PACKAGE) --fix

.PHONY: format
format:
	ruff format $(PACKAGE)
