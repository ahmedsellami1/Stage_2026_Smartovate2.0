"""
config.py — Chargement centralisé de la configuration et du logging
pour smartovate-ai-reviewer.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# GitHub App
# ---------------------------------------------------------------------------
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

# US 3.2 — identifiant utilisé pour détecter une mention de l'agent dans un
# commentaire (ex: "@smartovate-ai-reviewer explique ce code").
GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG", "smartovate-ai-reviewer")

# Lecture directe de la clé privée depuis la variable d'environnement
_raw_private_key = os.getenv("GITHUB_PRIVATE_KEY")

if _raw_private_key:
    # Formate correctement les sauts de ligne si la clé est transmise sur une ligne
    GITHUB_PRIVATE_KEY = _raw_private_key.replace("\\n", "\n")
else:
    # Option de secours : lecture depuis un fichier sur le disque
    GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH", "secrets/private-key.pem")
    if os.path.exists(GITHUB_PRIVATE_KEY_PATH):
        with open(GITHUB_PRIVATE_KEY_PATH, "r", encoding="utf-8") as _f:
            GITHUB_PRIVATE_KEY = _f.read()
    else:
        GITHUB_PRIVATE_KEY = ""

# ---------------------------------------------------------------------------
# LLM (Gemini, Anthropic ou OpenAI)
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Modèle par défaut dynamique selon le provider retenu
if LLM_PROVIDER == "gemini":
    _MODELE_PAR_DEFAUT = "gemini-2.5-flash"
elif LLM_PROVIDER == "anthropic":
    _MODELE_PAR_DEFAUT = "claude-3-5-sonnet-20241022"
else:
    _MODELE_PAR_DEFAUT = "gpt-4o"

LLM_MODEL = os.getenv("LLM_MODEL", _MODELE_PAR_DEFAUT)

# Seuil de tokens par appel LLM (marge de sécurité).
MAX_TOKENS_PAR_APPEL = int(os.getenv("MAX_TOKENS_PAR_APPEL", "50000"))

# ---------------------------------------------------------------------------
# Logging partagé
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ---------------------------------------------------------------------------
# Jira Integration (US 4.2)
# ---------------------------------------------------------------------------
JIRA_DOMAIN = os.getenv("JIRA_DOMAIN", "")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "")


def get_logger(nom: str) -> logging.Logger:
    return logging.getLogger(nom)