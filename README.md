# smartovate-ai-reviewer

Agent IA automatisé de revue de code pour Pull Requests — **Smartovate Ltd**

Ce projet met en place une GitHub App qui écoute les événements de Pull Request et de mention en commentaire, publie automatiquement des commentaires de revue de code générés par un LLM, et répond aux questions des développeurs qui la mentionnent.

---

## ⚠️ Sécurité — à lire avant tout

- Ne **jamais** committer `.env` ni `secrets/*.pem` (déjà exclus par `.gitignore`).
- Ne **jamais** partager `.env` réel, même avec un outil d'IA ou dans un zip de sauvegarde : utilisez `.env.example` (gabarit vide) pour tout partage.
- Si un secret a été exposé (partagé par erreur, commité, collé dans un chat) : **considérez-le compromis et régénérez-le immédiatement** (GitHub App : Webhook secret + Private key, clés Gemini, token Jira).

---

## Configuration de la GitHub App

Lors de la création de la GitHub App sur [github.com/settings/apps](https://github.com/settings/apps) :

**Permissions (Repository)**

| Permission      | Niveau          |
|-----------------|-----------------|
| Contents        | Read-only       |
| Pull requests   | Read & write    |
| Metadata        | Read-only       |

**Événements souscrits (Subscribe to events)**
- `Pull request`
- `Issue comment`

---

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/smartovate/smartovate-ai-reviewer.git
cd smartovate-ai-reviewer
```

### 2. Configurer les variables d'environnement

```bash
cp .env.example .env
```

Ouvrir `.env` et renseigner les vraies valeurs (voir `.env.example` pour la liste complète : App GitHub, clé(s) Gemini, Jira optionnel).

### 3. Placer la clé privée

Deux options prises en charge par `config.py` :
- Coller le contenu du `.pem` directement dans `GITHUB_PRIVATE_KEY` (avec `\n` pour les sauts de ligne), ou
- Placer le fichier dans `secrets/private-key.pem` et laisser `GITHUB_PRIVATE_KEY` vide (repli automatique sur `GITHUB_PRIVATE_KEY_PATH`).

### 4. Lancer le serveur

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Exposer le serveur en local avec [ngrok](https://ngrok.com/) (`ngrok http 8000`) et reporter l'URL générée (`https://xxxx.ngrok-free.app/webhook`) dans les paramètres de la GitHub App.

---

## Architecture du code

| Fichier                | Rôle                                                              | Classe UML             |
|-------------------------|--------------------------------------------------------------------|-------------------------|
| `main.py`               | Routes FastAPI, orchestration, traitement en arrière-plan          | `WebhookServer` (façade)|
| `security.py`           | Vérification de la signature HMAC du webhook                      | `WebhookServer.verifierSignature()` |
| `github_client.py`      | Authentification GitHub App, diff, publication de Review et de commentaires simples | `WebhookServer` (accès GitHub) |
| `diff_parser.py`        | Parsing du diff, filtrage des fichiers non pertinents              | `Diff`, `FichierModifie` |
| `llm_client.py`         | Prompt système, chunking (Bug 1), appel Gemini, réponse contextuelle (US 3.2) | `AgentIA` |
| `review_validator.py`   | Validation des lignes (Bug 2), filtrage des bugs critiques (bonus) | `CommentaireRevue.estLigneValide()` |
| `jira_client.py`        | Création de tickets Jira (**bonus, hors périmètre officiel**)      | —                       |
| `config.py`             | Variables d'environnement et logging partagés                      | —                       |

Le traitement des PR et des mentions est exécuté en tâche d'arrière-plan (`BackgroundTasks`) : GitHub reçoit une réponse `200` immédiate (~50ms), pendant que l'analyse LLM (5-10s) se déroule après.

---

## État d'avancement (backlog cahier des charges)

- [x] **US 1.1** — Création de la GitHub App
- [x] **US 1.2** — Mise en place du serveur backend
- [x] **US 2.1** — Extraction des modifications de code
- [x] **US 2.2** — Analyse du code par le LLM
- [x] **US 3.1** — Publication des commentaires sur la PR (Review + suggestions Markdown)
- [x] **US 3.2** — Gestion des commandes textuelles (mention de l'agent)
- [ ] **US 4.1** — Déploiement sur le cloud *(pas commencé — voir note ci-dessous)*
- [ ] **US 4.2** — Optimisation des coûts et des limites de requêtes *(pas commencé)*

**Correctifs des bugs anticipés :**
- **Bug 1** (dépassement de tokens) → `llm_client.decouper_en_lots()`
- **Bug 2** (ligne de commentaire invalide) → `review_validator.valider_commentaires()` détecte, `github_client.poster_revue_pr()` reclasse en commentaire global
- **Bug 3** (boucle infinie du bot) → `main.traiter_commande_textuelle_async()` ignore tout événement dont `sender.type == "Bot"`, avant même de regarder le contenu du message

### ⚠️ Écarts assumés par rapport au cahier des charges

1. **Provider LLM** : le cahier des charges spécifie OpenAI (GPT-4o) ou Anthropic (Claude 3.5 Sonnet). L'implémentation actuelle utilise **Gemini** (avec bascule multi-clés). Écart à justifier explicitement auprès de l'encadrant.
2. **Fonctionnalités bonus hors périmètre** : classification de sévérité des bugs et création automatique de tickets Jira (`jira_client.py`). Le cahier des charges ne mentionne Jira que comme outil de gestion de projet, pas comme intégration applicative. À présenter comme un ajout personnel, pas comme la réalisation des US 4.1/4.2 officielles (qui concernent le déploiement cloud et le rate limiting, toujours à faire).

---

## Prochaine étape

Les vraies **US 4.1** (Dockerfile, déploiement AWS/Azure) et **US 4.2** (limite de taille de PR >500 lignes, cache/rate limiting) du cahier des charges restent à implémenter.
