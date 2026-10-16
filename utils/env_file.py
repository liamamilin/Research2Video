"""Load the project's .env so the file wins over inherited environment values.

Why this is not a bare ``load_dotenv`` call
-------------------------------------------
``load_dotenv`` defaults to ``override=False``: a variable already present in
``os.environ`` beats the file. That default assumes the environment is the
operator's deliberate choice, which is true for a service configured by its
orchestrator -- and wrong here, because the Web console writes API keys straight
into .env and tells the user they take effect.

The failure it caused: the launchd gateway stays up for weeks and loads .env
into its own environment once, at startup. Every app it later spawns inherits
that snapshot (``subprocess.Popen(..., env={**os.environ})``), so a key saved in
the console was shadowed on restart by the value from whenever the gateway
booted -- the user typed a new key, reopened the app, and saw the old one.

The name of this module is deliberately explicit: the *file* is the source of
truth for the keys it contains. Variables the file does not mention are still
taken from the environment, so deployment concerns (AI_RESEARCH_ENV,
AI_RESEARCH_WEB_CONFIG) keep working.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv

# The JWT signing secret is different: the README tells operators to set it in
# the environment for production, so an explicit value wins even over the file.
# The console never manages this key, and losing it would sign every user out.
_ENV_OWNED_BY_OPERATOR = ("AI_RESEARCH_SECRET_KEY", "WEB_SECRET_KEY")


def load_env(path: Union[str, Path]) -> Optional[str]:
    """Load ``path`` into the process environment, file-first.

    Returns the path that was loaded, or ``None`` when it does not exist (a
    missing .env is normal for a container configured purely by environment
    variables).

    Keys present in the file overwrite inherited values; keys absent from it are
    left alone. ``AI_RESEARCH_SECRET_KEY``/``WEB_SECRET_KEY`` are restored after
    the load when the operator supplied them explicitly.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return None

    operator_secret = {
        name: os.environ[name]
        for name in _ENV_OWNED_BY_OPERATOR
        if os.environ.get(name)
    }

    load_dotenv(env_path, override=True)

    # Put the operator's secret back: it is the one value the environment, not
    # the file, is documented to own.
    for name, value in operator_secret.items():
        os.environ[name] = value
    # Keep the two spellings in sync so whichever one settings.py reads is right.
    if operator_secret.get("WEB_SECRET_KEY") and not operator_secret.get(
        "AI_RESEARCH_SECRET_KEY"
    ):
        os.environ["AI_RESEARCH_SECRET_KEY"] = operator_secret["WEB_SECRET_KEY"]

    return str(env_path)
