"""
github_client.py — Authentification GitHub App (JWT + token d'installation)
et accès à l'API GitHub pour récupérer le diff et poster des commentaires sur une Pull Request.

Regroupe les responsabilités liées à GitHub de la classe WebhookServer
(US 1.1, US 2.1, Epic 3) : c'est le seul module qui parle directement à l'API GitHub.
"""

import time

import jwt
import requests

from config import GITHUB_APP_ID, GITHUB_PRIVATE_KEY, get_logger

logger = get_logger(__name__)


def generate_jwt() -> str:
    """
    Génère un JWT signé RS256 à partir de la clé privée de la GitHub App.

    Le payload respecte les exigences de GitHub :
      - iat : timestamp actuel - 60 s (tolérance de décalage d'horloge)
      - exp : timestamp actuel + 600 s (durée de vie maximale autorisée par GitHub)
      - iss : App ID de la GitHub App

    :return: JWT encodé sous forme de chaîne de caractères.
    :raises ValueError: Si GITHUB_APP_ID ou GITHUB_PRIVATE_KEY n'est pas configuré.
    """
    if not GITHUB_APP_ID:
        raise ValueError("GITHUB_APP_ID n'est pas configuré dans les variables d'environnement.")

    if not GITHUB_PRIVATE_KEY:
        raise ValueError("GITHUB_PRIVATE_KEY est vide ou non configurée dans les variables d'environnement.")

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": GITHUB_APP_ID,
    }

    return jwt.encode(payload, GITHUB_PRIVATE_KEY, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """
    Échange le JWT de la GitHub App contre un token d'installation temporaire.

    Ce token permet d'agir au nom de l'installation sur un dépôt précis,
    avec les permissions définies lors de la création de la GitHub App.

    :param installation_id: ID de l'installation GitHub (présent dans le payload webhook).
    :return: Token d'accès temporaire sous forme de chaîne.
    :raises RuntimeError: Si l'API GitHub ne renvoie pas un statut 201.
    """
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {generate_jwt()}",
        "Accept": "application/vnd.github+json",
    }

    response = requests.post(url, headers=headers, timeout=10)

    if response.status_code != 201:
        raise RuntimeError(
            f"Impossible d'obtenir le token d'installation "
            f"(HTTP {response.status_code}) : {response.text}"
        )

    return response.json()["token"]


def get_pr_diff(owner: str, repo: str, pr_number: int, token: str) -> str:
    """
    Récupère le diff unifié d'une Pull Request via l'API GitHub.

    L'en-tête Accept: application/vnd.github.v3.diff demande à GitHub
    de renvoyer le diff au format texte unifié (patch) plutôt que du JSON.

    :param owner:     Propriétaire du dépôt (utilisateur ou organisation).
    :param repo:      Nom du dépôt.
    :param pr_number: Numéro de la Pull Request.
    :param token:     Token d'installation obtenu via get_installation_token().
    :return: Diff au format texte unifié.
    :raises RuntimeError: Si l'API GitHub renvoie une erreur.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.diff",
    }

    response = requests.get(url, headers=headers, timeout=15)

    if not response.ok:
        raise RuntimeError(
            f"Erreur lors de la récupération du diff PR #{pr_number} "
            f"(HTTP {response.status_code}) : {response.text}"
        )

    return response.text


def poster_revue_pr(
    owner: str,
    repo: str,
    pr_number: int,
    commit_id: str,
    token: str,
    commentaires: list[dict],
) -> bool:
    """
    Poste une Pull Request Review sur GitHub avec les commentaires inline et globaux.

    :param owner: Propriétaire du dépôt.
    :param repo: Nom du dépôt.
    :param pr_number: Numéro de la PR.
    :param commit_id: SHA du dernier commit (head sha) de la PR.
    :param token: Token d'installation GitHub.
    :param commentaires: Liste des commentaires validés par review_validator.
    :return: True si la publication a réussi, False sinon.
    """
    if not commentaires:
        logger.info("Aucun commentaire à poster sur la PR #%d.", pr_number)
        return True

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    inline_comments = []
    commentaires_hors_ligne = []

    for c in commentaires:
        corps = f"**[Smartovate AI Reviewer]**\n\n{c['contenu']}"
        if c.get("suggestion"):
            corps += f"\n\n```suggestion\n{c['suggestion']}\n```"

        if c.get("ligne_valide", True):
            inline_comments.append(
                {
                    "path": c["fichier"],
                    "line": c["ligne"],
                    "side": "RIGHT",
                    "body": corps,
                }
            )
        else:
            commentaires_hors_ligne.append(
                f"- **{c.get('fichier', 'Général')}** (ligne {c.get('ligne', '?')}) : {c['contenu']}"
            )

    body_general = "🤖 **Revue de code automatique effectuée par Smartovate AI Reviewer.**"
    if commentaires_hors_ligne:
        body_general += "\n\n### Remarques générales (hors lignes modifiées) :\n" + "\n".join(
            commentaires_hors_ligne
        )

    payload = {
        "commit_id": commit_id,
        "body": body_general,
        "event": "COMMENT",
        "comments": inline_comments,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code in (200, 201):
            logger.info(
                "Revue envoyée avec succès sur la PR #%d (%d inline, %d globaux).",
                pr_number,
                len(inline_comments),
                len(commentaires_hors_ligne),
            )
            return True
        else:
            logger.error(
                "Échec de la publication de la revue sur PR #%d (HTTP %d) : %s",
                pr_number,
                response.status_code,
                response.text,
            )
            return False
    except Exception as exc:
        logger.error(
            "Erreur réseau lors de la publication de la revue sur PR #%d : %s",
            pr_number,
            exc,
        )
        return False


def poster_commentaire_simple(owner: str, repo: str, pr_number: int, token: str, corps: str) -> bool:
    """
    US 3.2 — Poste un simple commentaire (non lié à une ligne précise) dans
    le fil de discussion d'une Pull Request, en réponse à une mention de
    l'agent (ex: "@smartovate-ai-reviewer explique ce code").

    Note technique : sur GitHub, une Pull Request est aussi une "issue" au
    sens de l'API. On utilise donc l'endpoint /issues/{n}/comments (et non
    /pulls/{n}/reviews, réservé aux revues structurées avec commentaires
    de ligne — voir poster_revue_pr ci-dessus).

    :param owner: Propriétaire du dépôt.
    :param repo: Nom du dépôt.
    :param pr_number: Numéro de la PR (identique au numéro d'issue).
    :param token: Token d'installation GitHub.
    :param corps: Texte de la réponse à publier (Markdown accepté).
    :return: True si la publication a réussi, False sinon.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"body": corps}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 201:
            logger.info("Réponse à la mention postée avec succès sur la PR #%d.", pr_number)
            return True
        logger.error(
            "Échec de la publication du commentaire sur PR #%d (HTTP %d) : %s",
            pr_number, response.status_code, response.text,
        )
        return False
    except Exception as exc:
        logger.error(
            "Erreur réseau lors de la publication du commentaire sur PR #%d : %s",
            pr_number, exc,
        )
        return False