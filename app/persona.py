from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = PROJECT_ROOT / "prompts" / "persona.md"

def load_persona() -> str:
    return PERSONA_PATH.read_text()