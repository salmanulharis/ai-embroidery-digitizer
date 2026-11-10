"""Optional AI assistance with user-provided keys. The app never depends on it: every
task has a built-in algorithm that is used when no AI service is configured or an AI
call fails.

Layout:
  base.py         provider interface, credentials, safe HTTP, model ranking helpers
  registry.py     provider registry, live model lists (cached), model choice
  providers/      one module per AI service - add a file to add a provider
  builtin.py      non-AI analysis and stitch review (the default)
  prompts.py      prompts for each task
  tasks.py        clean-up, settings suggestions, stitch review
"""

from . import providers  # noqa: F401  (registers the providers)
from .base import EDIT, VISION, AIError, Credentials
from .registry import PROVIDERS, catalog, get, register

__all__ = ["AIError", "Credentials", "EDIT", "VISION", "PROVIDERS", "catalog", "get", "register"]
