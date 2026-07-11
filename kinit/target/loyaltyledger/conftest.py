"""Ensures `ledger` is importable from tests without installing a package.

pytest adds the directory containing this conftest.py to sys.path during
collection (since there is no __init__.py making this a package), which
lets `tests/test_ledger.py` do `from ledger import ...`.
"""
