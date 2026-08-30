"""
diff_parser.py — Parsing et filtrage du diff unifié d'une Pull Request.

Correspond aux classes Diff et FichierModifie du diagramme de classes
(US 2.1 : Extraction des modifications de code).
"""

import fnmatch
from unidiff import PatchSet
from config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Liste des fichiers à exclure du diff.
# Les patterns supportent les wildcards via fnmatch (ex: "*.png").
# Correspond à FichierModifie.estPertinent() dans le diagramme de classes.
#
# NOTE : cette liste a été volontairement RESTREINTE par rapport à une
# version antérieure qui excluait aussi *.md, *.txt, *.json, *.yml, *.yaml
# et .gitignore. Ces types de fichiers peuvent contenir de vraies
# modifications applicatives (config CI/CD, manifestes Kubernetes, données
# métier en JSON...) que l'agent doit pouvoir analyser — le cahier des
# charges ne cite que "package-lock.json, images" comme exemples de
# fichiers non pertinents (US 2.1), pas la documentation ou la config.
# ---------------------------------------------------------------------------
FICHIERS_EXCLUS = [
    # Verrous de dépendances (générés automatiquement, non pertinents pour une revue)
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "composer.lock",
    "*.lock",

    # Images et médias (binaires, non analysables par le LLM)
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.svg",
    "*.gif",
    "*.ico",
    "*.pdf",
    "*.webp",

    # Fichiers minifiés et bundles générés (non lisibles/pertinents)
    "*.min.js",
    "*.min.css",
    "*.map",

    # Sécurité : ne jamais envoyer le contenu d'un fichier .env à un LLM externe
    ".env*",
]


def parser_diff(diff_text: str) -> list[dict]:
    """
    Parse un diff unifié et retourne uniquement les fichiers pertinents.

    Les fichiers dont le nom correspond à un pattern de FICHIERS_EXCLUS
    (binaires, lockfiles, assets, fichiers minifiés, documentation) sont ignorés.

    Pour chaque fichier conservé, on extrait :
      - "chemin"           : chemin relatif du fichier modifié
      - "lignes_modifiees" : liste de dicts {"numero": int, "contenu": str}
                            où "contenu" est le texte réel de la ligne ajoutée.
      - "diff_brut"        : le contenu textuel brut du patch pour ce fichier.

    :param diff_text: Diff au format texte unifié (obtenu via get_pr_diff).
    :return: Liste de dicts, un par fichier pertinent.
    """
    if not diff_text or not diff_text.strip():
        logger.info("Aucun diff à parser (diff vide).")
        return []

    try:
        patch_set = PatchSet(diff_text)
    except Exception as exc:
        logger.error("Erreur lors du parsing du diff unifié avec unidiff : %s", exc)
        return []

    fichiers_pertinents = []
    fichiers_ignores = []

    for fichier_patche in patch_set:
        chemin = fichier_patche.path

        # Vérification si le fichier doit être exclu
        if any(fnmatch.fnmatch(chemin, pattern) for pattern in FICHIERS_EXCLUS):
            fichiers_ignores.append(chemin)
            continue

        lignes_modifiees = [
            {
                "numero": ligne.target_line_no,
                "contenu": ligne.value.rstrip("\n"),
            }
            for hunk in fichier_patche
            for ligne in hunk
            if ligne.is_added and ligne.target_line_no is not None
        ]

        # Reconstitution du diff brut du fichier pour le LLM
        diff_brut = str(fichier_patche)

        fichiers_pertinents.append({
            "chemin": chemin,
            "lignes_modifiees": lignes_modifiees,
            "diff_brut": diff_brut,
        })

    if fichiers_ignores:
        logger.info("Fichiers ignorés lors de l'analyse (%d) : %s", len(fichiers_ignores), fichiers_ignores)

    return fichiers_pertinents