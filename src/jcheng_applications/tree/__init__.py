"""Opt-in py_trees task runtime.

The package itself imports no py_trees module. ``tree.runtime`` does, so the
default linear engine and the repository gate do not depend on the optional
``tree`` extra.
"""


class TreeEngineUnavailable(ValueError):
    """The optional py_trees runtime is not installed."""
