import os
from pathlib import Path


def credential_file(name):
    credentials_directory = os.getenv("CREDENTIALS_DIRECTORY")
    if not credentials_directory:
        raise RuntimeError(f"CREDENTIALS_DIRECTORY must contain the {name} credential.")
    return Path(credentials_directory) / name


def cookies_file():
    return os.getenv("PYTR_MCP_COOKIES_FILE", str(credential_file("cookies")))


def credentials():
    try:
        number, pin = credential_file("login").read_text().strip().split(":", maxsplit=1)
        return number, pin
    except ValueError:
        raise RuntimeError("The login credential must contain <number>:<pin>.")
