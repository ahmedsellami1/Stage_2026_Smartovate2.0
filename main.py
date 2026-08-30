"""
main.py — Serveur FastAPI, point d'entrée de l'application.

US 1.2 : Réception et validation des webhooks GitHub (avec exécution asynchrone)
US 2.1 : Extraction et parsing du diff d'une Pull Request
US 2.2 : Analyse du code par le LLM (avec chunking, Bug 1)
Epic 3 :
  US 3.1 : Publication des commentaires de revue sur la PR (avec reclassement
           en commentaire global des lignes invalides, Bug 2)
  US 3.2 : Gestion des commandes textuelles (mention de l'agent), avec
           filtrage des événements émis par un bot pour éviter la boucle
           infinie de réponses (Bug 3)

Fonctionnalités bonus HORS PÉRIMÈTRE officiel du cahier des charges :
classification de sévérité et création automatique de tickets Jira
(voir review_validator.filtrer_bugs_critiques et jira_client.py). Les
vraies US 4.1 (déploiement cloud) et US 4.2 (optimisation des coûts /
rate limiting) restent à implémenter séparément.
"""

import json

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from config import GITHUB_APP_SLUG, GITHUB_WEBHOOK_SECRET, JIRA_DOMAIN, get_logger
from diff_parser import parser_diff
from github_client import (
    get_installation_token,
    get_pr_diff,
    poster_commentaire_simple,
    poster_revue_pr,
)
from jira_client import creer_ticket_jira_pour_bug
from llm_client import analyser_code, repondre_question_contextuelle
from review_validator import filtrer_bugs_critiques, valider_commentaires
from security import verify_signature

logger = get_logger(__name__)

app = FastAPI(
    title="smartovate-ai-reviewer",
    description="Agent IA automatisé de revue de code pour Pull Requests — Smartovate Ltd",
    version="0.6.0",
)

# ---------------------------------------------------------------------------
# Idempotence des webhooks (en mémoire)
# ---------------------------------------------------------------------------
DELIVERIES_TRAITEES: set[str] = set()


def traiter_pull_request_async(payload: dict, event_type: str) -> None:
    """
    Fonction exécutée en arrière-plan par BackgroundTasks pour éviter
    le risque de timeout HTTP (10s) imposé par les webhooks GitHub.
    """
    try:
        installation_id = payload["installation"]["id"]
        full_name = payload["repository"]["full_name"]
        owner, repo = full_name.split("/", 1)
        pr_number = payload["pull_request"]["number"]
        head_sha = payload["pull_request"]["head"]["sha"]
        pr_url = payload["pull_request"].get("html_url", "")

        logger.info(
            "[Background] PR #%d détectée sur %s (action: %s) — démarrage de l'extraction du diff.",
            pr_number, full_name, payload.get("action"),
        )

        installation_token = get_installation_token(installation_id)
        logger.info("[Background] Token d'installation obtenu avec succès.")

        diff_text = get_pr_diff(owner, repo, pr_number, installation_token)
        logger.info("[Background] Diff de la PR #%d récupéré (%d octets).", pr_number, len(diff_text))

        fichiers = parser_diff(diff_text)
        chemins = [f["chemin"] for f in fichiers]
        logger.info(
            "[Background] Diff parsé : %d fichier(s) pertinent(s) trouvé(s) : %s",
            len(fichiers), chemins,
        )

        commentaires = analyser_code(fichiers)
        logger.info("[Background] Analyse LLM terminée : %d commentaire(s) généré(s).", len(commentaires))

        commentaires = valider_commentaires(commentaires, fichiers)
        nb_valides = sum(1 for c in commentaires if c["ligne_valide"])
        nb_invalides = len(commentaires) - nb_valides
        logger.info(
            "[Background] Validation terminée : %d commentaire(s) sur ligne valide, "
            "%d à reclasser en commentaire global.",
            nb_valides, nb_invalides,
        )

        # --- US 4.1 & 4.2 : Détection des bugs critiques, création de tickets Jira & lien GitHub ---
        bugs_critiques = filtrer_bugs_critiques(commentaires)
        if bugs_critiques:
            logger.warning(
                "ALERT US 4.1 : %d bug(s) CRITIQUE(S)/ÉLEVÉ(S) détecté(s) sur la PR #%d !",
                len(bugs_critiques), pr_number,
            )
            for b in bugs_critiques:
                logger.warning(
                    "  -> [%s] %s (ligne %s) : %s",
                    b.get("severite", "HIGH"), b.get("fichier", "?"), b.get("ligne", "?"), b.get("contenu", "")
                )
                # US 4.2 : Création automatique du ticket Jira
                issue_key = creer_ticket_jira_pour_bug(
                    bug=b,
                    pr_number=pr_number,
                    repo_full_name=full_name,
                    pr_url=pr_url,
                )

                # Ajout automatique du lien vers le ticket Jira dans le commentaire GitHub
                if issue_key and JIRA_DOMAIN:
                    jira_ticket_url = f"{JIRA_DOMAIN.rstrip('/')}/browse/{issue_key}"
                    b["contenu"] += f"\n\n🎫 **Ticket Jira créé :** [{issue_key}]({jira_ticket_url})"

        for c in commentaires:
            marqueur = "OK" if c["ligne_valide"] else "!! ligne invalide"
            sev = c.get("severite", "INFO")
            logger.info(
                "  [%s] [%s] %s ligne %s : %s",
                marqueur, sev, c.get("fichier", "?"), c.get("ligne", "?"), c.get("contenu", ""),
            )

        # --- Epic 3 : Publication des retours sur la PR GitHub ---
        if commentaires:
            poster_revue_pr(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                commit_id=head_sha,
                token=installation_token,
                commentaires=commentaires,
            )

    except Exception as exc:
        logger.error(
            "Erreur lors du traitement asynchrone de la PR (event=%s) : %s",
            event_type, exc,
        )


def traiter_commande_textuelle_async(payload: dict) -> None:
    """
    US 3.2 — Traite un commentaire mentionnant l'agent sur une Pull Request,
    exécuté en arrière-plan comme traiter_pull_request_async().

    Bug 3 (cahier des charges) : "L'agent IA répond à ses propres commentaires
    ou aux commentaires d'autres bots, créant une boucle infinie". On ignore
    donc systématiquement tout événement dont l'émetteur (sender) est un bot
    — condition placée en tout premier, avant même de regarder le contenu.
    """
    sender = payload.get("sender", {}) or {}
    sender_type = sender.get("type", "")
    sender_login = sender.get("login", "")

    # --- Bug 3 : ignorer tout événement émis par un bot (dont l'agent lui-même) ---
    if sender_type == "Bot":
        logger.info(
            "[Background] Commentaire émis par un bot ('%s') — ignoré pour éviter une boucle infinie (Bug 3).",
            sender_login,
        )
        return

    corps_commentaire = (payload.get("comment", {}) or {}).get("body", "") or ""
    mention = f"@{GITHUB_APP_SLUG}"

    if mention.lower() not in corps_commentaire.lower():
        logger.debug("Commentaire ne mentionnant pas l'agent — ignoré.")
        return

    # L'API GitHub traite les PR comme des issues ; on vérifie qu'il s'agit
    # bien d'une Pull Request et pas d'une issue classique.
    if "pull_request" not in (payload.get("issue", {}) or {}):
        logger.info("[Background] Mention détectée sur une issue classique (pas une PR) — ignorée.")
        return

    try:
        installation_id = payload["installation"]["id"]
        full_name = payload["repository"]["full_name"]
        owner, repo = full_name.split("/", 1)
        pr_number = payload["issue"]["number"]
        question = corps_commentaire.replace(mention, "", 1).strip()

        logger.info(
            "[Background] Mention de l'agent détectée sur PR #%d (%s) par '%s' — question : '%s'",
            pr_number, full_name, sender_login, question,
        )

        installation_token = get_installation_token(installation_id)

        # Récupère le diff de la PR pour donner du contexte à la réponse
        diff_text = get_pr_diff(owner, repo, pr_number, installation_token)
        fichiers = parser_diff(diff_text)

        reponse = repondre_question_contextuelle(question, fichiers)

        poster_commentaire_simple(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            token=installation_token,
            corps=reponse,
        )

    except Exception as exc:
        logger.error("Erreur lors du traitement de la commande textuelle : %s", exc)


@app.get("/")
async def health_check():
    """Point de terminaison de santé (health check)."""
    return {"status": "ok"}


@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    payload_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    if not GITHUB_WEBHOOK_SECRET:
        logger.error("GITHUB_WEBHOOK_SECRET n'est pas configuré dans .env")
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    if not verify_signature(payload_body, signature_header, GITHUB_WEBHOOK_SECRET):
        logger.warning("Webhook reçu avec une signature invalide — requête rejetée.")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(payload_body)
    event_type = request.headers.get("X-GitHub-Event", "unknown")

    logger.info("Événement GitHub reçu : type='%s'", event_type)

    delivery_id = request.headers.get("X-GitHub-Delivery")
    if delivery_id and delivery_id in DELIVERIES_TRAITEES:
        logger.info(
            "Livraison %s déjà traitée précédemment — événement ignoré (idempotence).",
            delivery_id,
        )
        return {"status": "already_processed", "event": event_type}
    if delivery_id:
        DELIVERIES_TRAITEES.add(delivery_id)

    if event_type == "pull_request" and payload.get("action") in ("opened", "synchronize"):
        # Lancement de la tâche longue en arrière-plan
        background_tasks.add_task(traiter_pull_request_async, payload, event_type)
        
        # Réponse immédiate HTTP 200 OK à GitHub (~50ms)
        return {
            "status": "processing_in_background",
            "event": event_type,
            "pr_number": payload.get("pull_request", {}).get("number"),
        }

    # US 3.2 — Gestion des commandes textuelles (mention de l'agent)
    if event_type == "issue_comment" and payload.get("action") == "created":
        background_tasks.add_task(traiter_commande_textuelle_async, payload)

        return {
            "status": "processing_comment_in_background",
            "event": event_type,
            "issue_number": payload.get("issue", {}).get("number"),
        }

    return {"status": "received", "event": event_type}