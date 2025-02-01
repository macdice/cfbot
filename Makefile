format:
	ruff format

lint:
	ruff check

lint-fix:
	ruff check --fix

lint-fix-unsafe:
	ruff check --fix --unsafe-fixes

fix: format lint-fix-unsafe
