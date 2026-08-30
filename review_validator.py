"""
review_validator.py — Validation et filtrage des commentaires générés par l'agent IA.

Bug 2 (cahier des charges) : valider_commentaires() détecte les commentaires
placés sur une ligne hors diff.

Fonctionnalité bonus, HORS PÉRIMÈTRE du cahier des charges officiel :
filtrer_bugs_critiques() extrait les commentaires de sévérité HIGH/CRITICAL
pour alimenter la création automatique de tickets Jira (voir jira_client.py).
Ne pas confondre avec les vraies US 4.1 (Déploiement sur le Cloud) et
US 4.2 (Optimisation des coûts et des limites de requêtes) du cahier des
charges, qui restent à implémenter séparément (Dockerfile, déploiement
AWS/Azure, limite de taille de PR, cache/rate limiting).
"""

from config import get_logger

logger = get_logger(__name__)


def valider_commentaires(commentaires: list[dict], fichiers: list[dict]) -> list[dict]:
    """
    Croise chaque commentaire retourné par le LLM avec les lignes réellement
    modifiées du diff original.
    """
    lignes_par_fichier = {
        f["chemin"]: {l["numero"] for l in f["lignes_modifiees"]}
        for f in fichiers
    }

    commentaires_valides = []
    for c in commentaires:
        fichier = c.get("fichier")
        ligne = c.get("ligne")
        est_valide = fichier in lignes_par_fichier and ligne in lignes_par_fichier[fichier]

        if not est_valide:
            logger.warning(
                "Commentaire sur une ligne hors diff détecté : fichier='%s' ligne=%s "
                "— sera reclassé en commentaire global lors de la publication (Bug 2).",
                fichier, ligne,
            )

        commentaires_valides.append({**c, "ligne_valide": est_valide})

    return commentaires_valides


def filtrer_bugs_critiques(commentaires: list[dict]) -> list[dict]:
    """
    US 4.1 : Filtre la liste des commentaires pour n'extraire que ceux 
    déclarés avec une sévérité HIGH ou CRITICAL.
    """
    bugs_critiques = [
        c for c in commentaires
        if c.get("severite") in ("HIGH", "CRITICAL")
    ]
    return bugs_critiques