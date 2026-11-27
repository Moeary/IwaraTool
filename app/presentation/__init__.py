"""Presentation logic, free of any UI framework.

Modules here derive display data (texts, orderings, font stacks) from core
state.  They import neither Flet nor ``app.ui``, so they stay testable without
a window (``scripts/tests/ui/test_theme.py`` checks this).
"""
