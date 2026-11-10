"""Importing this package registers every built-in provider (in display order).

To add a provider: create a module here with a ``Provider`` subclass decorated with
``@register`` and import it below. Nothing else needs to change - the API, the AI
settings screen and the model lists pick it up automatically.
"""

from . import gemini, openai, openrouter, anthropic, ollama, custom  # noqa: F401,I001
