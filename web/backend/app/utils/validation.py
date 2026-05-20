"""
Input validation utilities for path-safety and sanitization.
"""

import re


# Allowed pattern: alphanumeric, hyphens, underscores, dots (no slashes, no ..)
_SAFE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')


def validate_simulation_id(simulation_id: str) -> str:
    """
    Validate that a simulation_id is safe for use in filesystem paths.

    Rejects any value containing path separators, parent-directory
    references, or characters outside the allowed set
    (alphanumeric, hyphen, underscore, dot).

    Args:
        simulation_id: The raw simulation ID from user input.

    Returns:
        The validated simulation_id (unchanged if valid).

    Raises:
        ValueError: If the simulation_id is empty, contains path
            traversal sequences, or uses disallowed characters.
    """
    if not simulation_id:
        raise ValueError("simulation_id must not be empty")

    if '..' in simulation_id:
        raise ValueError("simulation_id must not contain '..'")

    if '/' in simulation_id or '\\' in simulation_id:
        raise ValueError("simulation_id must not contain path separators")

    if not _SAFE_ID_PATTERN.match(simulation_id):
        raise ValueError(
            "simulation_id contains invalid characters; "
            "only alphanumeric, hyphen, underscore, and dot are allowed"
        )

    return simulation_id
