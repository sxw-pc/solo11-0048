PYTHON ?= python

.PHONY: demo test clean fixtures

demo:
	$(PYTHON) -m jitterlens demo

fixtures:
	$(PYTHON) -m jitterlens gen-fixtures

test:
	$(PYTHON) -m pytest

clean:
	rm -rf build .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find fixtures/generated -maxdepth 1 -type f -name 'mock-*' -delete
