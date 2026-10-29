.PHONY: setup dev test verify

setup:
	python3 mednik.py setup

dev:
	python3 mednik.py dev

test:
	python3 mednik.py test

verify:
	python3 mednik.py verify
