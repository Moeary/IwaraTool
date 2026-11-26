"""Presentation logic shared by both interfaces, free of any UI framework.

Modules here derive display data (texts, orderings, font stacks) from core
state.  They must import neither Qt nor Flet, so the PySide6 interface in
``app.ui`` and the Flet interface in ``app.ui_flet`` can both use them.
"""
