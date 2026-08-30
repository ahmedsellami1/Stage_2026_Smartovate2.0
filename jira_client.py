"""
jira_client.py — Communication avec l'API REST v3 de Jira.

Fonctionnalité bonus, HORS PÉRIMÈTRE du cahier des charges officiel : le
cahier des charges ne mentionne Jira que comme outil de gestion de projet
("Gestion de projet : Jira"), jamais comme intégration applicative de
l'agent. Ce module n'est donc pas une réalisation de l'US 4.2 officielle
(qui concerne l'optimisation des coûts et le rate limiting) — à présenter
comme un ajout personnel assumé dans le rapport de PFA.
"""

import requests
from config import (
    JIRA_API_TOKEN,
    JIRA_DOMAIN,
    JIRA_PROJECT_KEY,
    JIRA_USER_EMAIL,
    get_logger,
)

logger = get_logger(__name__)


def creer_ticket_jira_pour_bug(
    bug: dict, pr_number: int, repo_full_name: str, pr_url: str
) -> str | None:
    """
    Crée automatiquement un ticket de type 'Tâche' dans Jira lorsqu'un bug 
    critique ou élevé est identifié par l'agent IA.
    Retourne la clé du ticket créé (ex: 'KAN-1') ou None en cas d'échec.
    """
    if not all([JIRA_DOMAIN, JIRA_USER_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY]):
        logger.warning(
            "Configuration Jira incomplète dans .env — création de ticket ignorée."
        )
        return None

    url = f"{JIRA_DOMAIN.rstrip('/')}/rest/api/3/issue"
    auth = (JIRA_USER_EMAIL, JIRA_API_TOKEN)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    fichier = bug.get("fichier", "Inconnu")
    ligne = bug.get("ligne", "?")
    severite = bug.get("severite", "HIGH")
    contenu = bug.get("contenu", "Bug détecté par l'Agent IA")

    summary = f"[{severite}] Bug détecté dans PR #{pr_number} ({fichier}:{ligne})"

    # Structure Atlassian Document Format (ADF) pour l'API REST v3 de Jira
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "issuetype": {"name": "Tâche"},  # Adapté au type de ticket configuré dans votre projet Jira
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Un problème de sévérité {severite} a été détecté par Smartovate AI Reviewer.",
                            }
                        ],
                    },
                    {
                        "type": "bulletList",
                        "content": [
                            {
                                "type": "listItem",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {"type": "text", "text": f"Dépôt : {repo_full_name}"}
                                        ],
                                    }
                                ],
                            },
                            {
                                "type": "listItem",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {"type": "text", "text": f"Fichier : {fichier} (Ligne {ligne})"}
                                        ],
                                    }
                                ],
                            },
                            {
                                "type": "listItem",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {"type": "text", "text": f"Lien PR : {pr_url}"}
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Détails du problème :\n{contenu}",
                            }
                        ],
                    },
                ],
            },
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, auth=auth, timeout=10)
        if response.status_code == 201:
            data = response.json()
            issue_key = data.get("key")
            logger.info("Ticket Jira créé avec succès : %s (PR #%d)", issue_key, pr_number)
            return issue_key
        else:
            logger.error(
                "Échec de création du ticket Jira (HTTP %d) : %s",
                response.status_code,
                response.text,
            )
            return None
    except Exception as exc:
        logger.error("Erreur lors de l'appel à l'API Jira : %s", exc)
        return None