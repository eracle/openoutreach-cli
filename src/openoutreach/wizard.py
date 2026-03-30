"""Reusable step-by-step prompt wizard with Esc-to-go-back support."""

from __future__ import annotations

from dataclasses import dataclass

import questionary
from prompt_toolkit.key_binding import KeyBindings

_BACK = "\x1b"

_kb = KeyBindings()


@_kb.add("escape")
def _(event):
    event.app.exit(result=_BACK)


def _required_validator(val: str) -> bool | str:
    return True if val.strip() else "This field is required."


def _int_validator(val: str) -> bool | str:
    try:
        int(val)
        return True
    except ValueError:
        return "Please enter a valid integer."


# ---------------------------------------------------------------------------
# Question types
# ---------------------------------------------------------------------------


@dataclass
class Question:
    """Base for all question types. Subclasses override ``_build()``."""

    key: str
    message: str
    default: str | int | bool = ""
    required: bool = True

    def prompt(self, default, *, answers: dict | None = None) -> questionary.Question:
        return self._build(default, answers=answers)

    def _build(self, default, *, answers: dict | None = None) -> questionary.Question:
        raise NotImplementedError

    def clean(self, raw):
        """Normalise the raw answer before storing it."""
        return raw.strip() if isinstance(raw, str) else raw


class Text(Question):
    def _build(self, default, **_):
        return questionary.text(
            self.message, default=default, key_bindings=_kb,
            validate=_required_validator if self.required else None,
        )


class MultilineText(Question):
    """Multi-line text input. Esc+Enter to submit."""

    def _build(self, default, **_):
        return questionary.text(
            self.message, default=default, multiline=True, key_bindings=_kb,
            validate=_required_validator if self.required else None,
            instruction="(Esc+Enter to submit)",
        )


class Password(Question):
    def _build(self, default, **_):
        return questionary.password(
            self.message, default=default, key_bindings=_kb,
            validate=_required_validator if self.required else None,
        )


class Confirm(Question):
    """Yes/no confirmation prompt."""

    default: bool = True
    required: bool = False  # always produces a value

    def _build(self, default, **_):
        return questionary.confirm(
            self.message,
            default=default if isinstance(default, bool) else self.default,
            key_bindings=_kb,
        )


class IntText(Question):
    """Integer text input with validation."""

    default: int = 0
    required: bool = False  # always has a numeric default

    def _build(self, default, **_):
        return questionary.text(
            self.message, default=str(default), key_bindings=_kb,
            validate=_int_validator,
        )

    def clean(self, raw):
        return int(raw)


class Autocomplete(Question):
    """Type-to-filter searchable select. Choices resolved lazily."""

    def __init__(self, key: str, message: str, *, resolver: callable, default: str = ""):
        super().__init__(key=key, message=message, default=default)
        self.resolver = resolver

    def _build(self, default, *, answers: dict | None = None):
        choices = self.resolver(answers or {})
        if not choices:
            return questionary.text(self.message, default=default, key_bindings=_kb)
        return questionary.autocomplete(
            self.message, choices=choices, default=default, key_bindings=_kb,
            validate=lambda val: val in choices or "Please select a valid option",
        )


# ---------------------------------------------------------------------------
# Wizard runner
# ---------------------------------------------------------------------------


def ask(questions: list[Question]) -> dict | None:
    """Walk through questions. Esc to go back, Ctrl+C to cancel. Returns None if cancelled."""
    print("(Esc: back, Ctrl+C: cancel)\n")
    answers: dict = {}
    i = 0
    while i < len(questions):
        q = questions[i]
        default = answers.get(q.key, q.default)
        raw = q.prompt(default, answers=answers).ask()

        if raw is None:
            return None
        if raw == _BACK:
            i = max(0, i - 1)
            continue
        answers[q.key] = q.clean(raw)
        i += 1

    return answers
