from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
