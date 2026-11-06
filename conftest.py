"""Load the Home Assistant test plugin for the component tests.

pytest 9 accepts `pytest_plugins` only in a top-level conftest file.
"""

pytest_plugins = "pytest_homeassistant_custom_component"
