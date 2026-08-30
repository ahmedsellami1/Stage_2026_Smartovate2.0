"""
llm_client.py — Communication avec Gemini.

Fonctionnalités :
- Utilise le modèle défini dans LLM_MODEL.
- Alterne entre GEMINI_API_KEY, GEMINI_API_KEY_2 et GEMINI_API_KEY_3 en cas
  d'erreur 429 ou 503 (fallback).
- Attend automatiquement la durée demandée par l'API en cas de surcharge.
- Découpe le diff en lots pour respecter la limite de tokens (Bug 1 du
  cahier des charges) — RÉINTRODUIT après régression lors du passage à Gemini.
- Fournit une réponse contextuelle en langage naturel pour l'US 3.2
  (commande textuelle via mention de l'agent).

NOTE DE CONFORMITÉ : le cahier des charges (section "Technologies et Outils")
spécifie explicitement "API OpenAI (GPT-4o) ou API Anthropic (Claude 3.5
Sonnet)". L'usage de Gemini ici est un écart par rapport au document
contractuel — à justifier explicitement auprès de l'encadrant plutôt qu'à
laisser implicite.
"""

import json
import os
import re
import time

import requests

from config import (
    GEMINI_API_KEY,
    LLM_PROVIDER,
    MAX_TOKENS_PAR_APPEL,
    get_logger,
)

logger = get_logger(__name__)

GEMINI_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")

# Liste des clés API disponibles pour le basculement (Fallback)
GEMINI_API_KEYS = [
    key.strip()
    for key in [
        GEMINI_API_KEY,
        os.getenv("GEMINI_API_KEY_2"),
        os.getenv("GEMINI_API_KEY_3"),
    ]
    if key and key.strip()
]

# ---------------------------------------------------------------------------
# US 2.2 — Prompt système pour l'analyse de code
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
Vous êtes un expert en revue de code et en sécurité logicielle.
Votre tâche est d'analyser le diff d'une Pull Request et d'identifier :
1. Les bugs de sécurité (Injection SQL, Path Traversal, RCE, Secrets hardcodés, etc.).
2. Les erreurs de syntaxe, fautes de frappe ou variables non définies (NameError).
3. Les problèmes de performance ou de bonnes pratiques.

Pour chaque problème détecté, vous devez impérativement retourner un objet JSON dans le format suivant :
{
  "commentaires": [
    {
      "fichier": "nom_du_fichier.py",
      "ligne": 12,
      "severite": "CRITICAL",  // Options: CRITICAL, HIGH, MEDIUM, LOW, INFO
      "contenu": "Explication claire du problème.",
      "suggestion": "code_corrige_ou_recommandation"
    }
  ]
}

Règles strictes :
- Répondez EXCLUSIVEMENT sous forme de JSON valide. Ne rajoutez aucun texte hors du JSON.
- N'inventez pas de numéros de ligne qui ne sont pas dans le diff fourni.
"""

# ---------------------------------------------------------------------------
# US 3.2 — Prompt système pour répondre aux mentions de l'agent
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_QA = """
Vous êtes un développeur senior expert, assistant de revue de code.
Un développeur vous a mentionné dans un commentaire de Pull Request pour
vous poser une question sur le code modifié.

Répondez de manière claire, concise et pédagogique, en vous basant
UNIQUEMENT sur le contexte du diff fourni. N'inventez pas de code ou de
contexte absent du diff. Répondez en texte normal formaté en Markdown
(pas de JSON ici).
"""


def _extraire_retry_delay(response_text: str) -> int:
    """Extrait le délai d'attente (en secondes) retourné par l'API Gemini."""
    match = re.search(r'retryIn["\s:]+([0-9\.]+)s', response_text, re.IGNORECASE) or \
            re.search(r'retryDelay["\s:]+["\']?([0-9\.]+)s?', response_text, re.IGNORECASE) or \
            re.search(r'retry in ([0-9\.]+)s', response_text, re.IGNORECASE)
    if match:
        return int(float(match.group(1))) + 2
    return 35


def _appeler_gemini_avec_fallback(
    prompt: str,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.2,
    json_mode: bool = True,
) -> dict | None:
    """
    Appelle l'API Gemini avec bascule automatique entre les clés API
    configurées en cas de limitation (429) ou de surcharge (503).

    Paramétré par system_prompt / json_mode pour être réutilisable à la
    fois par analyser_code() (revue de code, JSON strict) et par
    repondre_question_contextuelle() (réponse en langage naturel, US 3.2).
    """
    if not GEMINI_API_KEYS:
        logger.error("Aucune clé API Gemini n'a été trouvée dans la configuration.")
        return None

    generation_config = {"temperature": temperature}
    if json_mode:
        generation_config["response_mime_type"] = "application/json"

    payload = {
        "contents": [
            {"parts": [{"text": f"{system_prompt}\n\n{prompt}"}]}
        ],
        "generationConfig": generation_config,
    }

    for idx, key in enumerate(GEMINI_API_KEYS):
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"

        try:
            response = requests.post(
                endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                if idx > 0:
                    logger.info("Succès de l'appel Gemini via la clé de secours n°%d", idx + 1)
                return response.json()

            elif response.status_code in (429, 503):
                wait_time = _extraire_retry_delay(response.text)
                logger.warning(
                    "Limitation/Surcharge API Gemini (HTTP %d) sur la clé n°%d. Attente recommandée : %ds.",
                    response.status_code, idx + 1, wait_time
                )
                continue

            else:
                logger.error("Erreur API Gemini (HTTP %d) : %s", response.status_code, response.text)

        except Exception as exc:
            logger.error("Exception lors de l'appel Gemini avec la clé n°%d : %s", idx + 1, exc)

    wait_time = 35
    logger.warning("Toutes les clés Gemini sont en limite de quota. Pause de %d secondes...", wait_time)
    time.sleep(wait_time)

    try:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEYS[0]}"
        response = requests.post(endpoint, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()
        logger.error("Dernier essai après pause échoué (HTTP %d) : %s", response.status_code, response.text)
    except Exception as exc:
        logger.error("Exception lors du dernier essai Gemini : %s", exc)

    return None


def _construire_prompt_fichiers(fichiers: list[dict]) -> str:
    """Construit le bloc de texte listant les fichiers et leur diff brut."""
    prompt_fichiers = ""
    for f in fichiers:
        chemin = f.get("chemin") or f.get("filename") or f.get("fichier") or "inconnu.py"
        diff_contenu = (
            f.get("diff_brut")
            or f.get("diff")
            or f.get("patch")
            or f.get("content")
            or str(f)
        )
        prompt_fichiers += f"\n--- Fichier: {chemin} ---\n{diff_contenu}\n"
    return prompt_fichiers


def estimer_tokens(texte: str) -> int:
    """Estimation grossière du nombre de tokens : 1 token ≈ 4 caractères."""
    return len(texte) // 4


def decouper_en_lots(fichiers: list[dict], max_tokens: int = MAX_TOKENS_PAR_APPEL) -> list[list[dict]]:
    """
    Découpe la liste de fichiers en lots dont le prompt formaté estimé ne
    dépasse pas max_tokens, afin d'éviter le dépassement de la fenêtre de
    contexte du LLM (Bug 1 du cahier des charges : "Découper le diff par
    fichier si un fichier est trop grand").

    Un fichier dont le contenu dépasse À LUI SEUL max_tokens est envoyé
    seul dans son propre lot plutôt que rejeté, avec un avertissement —
    le modèle peut échouer sur ce lot précis, mais les autres continuent.
    """
    lots: list[list[dict]] = []
    lot_courant: list[dict] = []
    tokens_lot_courant = 0

    for fichier in fichiers:
        tokens_fichier = estimer_tokens(_construire_prompt_fichiers([fichier]))

        if tokens_fichier > max_tokens:
            logger.warning(
                "Fichier '%s' dépasse à lui seul la limite de tokens (~%d > %d) "
                "— traité dans son propre lot (risque d'échec si le modèle refuse le contexte).",
                fichier.get("chemin"), tokens_fichier, max_tokens,
            )
            lots.append([fichier])
            continue

        if lot_courant and tokens_lot_courant + tokens_fichier > max_tokens:
            lots.append(lot_courant)
            lot_courant = []
            tokens_lot_courant = 0

        lot_courant.append(fichier)
        tokens_lot_courant += tokens_fichier

    if lot_courant:
        lots.append(lot_courant)

    return lots


def analyser_code(fichiers: list[dict]) -> list[dict]:
    """
    US 2.2 — Envoie le diff au LLM, en le découpant en plusieurs lots si
    nécessaire (Bug 1), et retourne la liste agrégée des commentaires.
    """
    if not fichiers:
        logger.info("Aucun fichier à analyser par le LLM.")
        return []

    lots = decouper_en_lots(fichiers)
    if len(lots) > 1:
        logger.info(
            "Diff découpé en %d lot(s) pour respecter la limite de tokens (Bug 1).",
            len(lots),
        )

    tous_les_commentaires: list[dict] = []

    for i, lot in enumerate(lots, start=1):
        prompt_fichiers = _construire_prompt_fichiers(lot)
        logger.info(
            "Lot %d/%d : ~%d tokens estimés, %d fichier(s). Provider: %s (Modèle: %s)",
            i, len(lots), estimer_tokens(prompt_fichiers), len(lot), LLM_PROVIDER, GEMINI_MODEL,
        )

        raw_response = _appeler_gemini_avec_fallback(prompt_fichiers, system_prompt=SYSTEM_PROMPT, json_mode=True)

        if not raw_response:
            logger.error("L'analyse LLM n'a renvoyé aucun résultat pour le lot %d/%d.", i, len(lots))
            continue

        try:
            text_content = (
                raw_response.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            data = json.loads(text_content)
            tous_les_commentaires.extend(data.get("commentaires", []))
        except Exception as exc:
            logger.error(
                "Erreur lors du traitement du JSON retourné par Gemini (lot %d/%d) : %s",
                i, len(lots), exc,
            )
            continue

    return tous_les_commentaires


def repondre_question_contextuelle(question: str, fichiers: list[dict]) -> str:
    """
    US 3.2 — Génère une réponse en langage naturel à une question posée
    par un développeur qui mentionne l'agent dans un commentaire de PR.

    :param question: Texte de la question, mention de l'agent déjà retirée.
    :param fichiers:  Fichiers pertinents du diff de la PR (contexte).
    :return: Réponse textuelle (Markdown) à publier telle quelle sur GitHub.
    """
    contexte = _construire_prompt_fichiers(fichiers) if fichiers else "(aucun fichier pertinent trouvé dans le diff)"
    prompt = f"Contexte du diff de la Pull Request :\n{contexte}\n\nQuestion du développeur : {question}"

    raw_response = _appeler_gemini_avec_fallback(
        prompt, system_prompt=SYSTEM_PROMPT_QA, temperature=0.3, json_mode=False,
    )

    if not raw_response:
        return "⚠️ Une erreur est survenue lors de la génération de la réponse. Réessaie plus tard."

    try:
        return (
            raw_response.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "Je n'ai pas pu générer de réponse.")
        )
    except Exception as exc:
        logger.error("Erreur lors de l'extraction de la réponse contextuelle : %s", exc)
        return "⚠️ Une erreur est survenue lors de la génération de la réponse."
