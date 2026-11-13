"""The replay harness runs as a pytest session so it gets a real (test) Home Assistant.

scripts/replay.py starts it with the working directory set to the version being replayed, so
`custom_components.sentient` is that version's code; this file and runner.py come from the current tree.
"""
import os
import sys

import pytest

# the version being replayed: its custom_components, imported before Home Assistant's test plugin brings in its own
sys.path.insert(0, os.getcwd())
import custom_components  # noqa: E402,F401
import custom_components.sentient  # noqa: E402,F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
