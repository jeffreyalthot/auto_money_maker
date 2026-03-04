# Local Firefox Survey Assistant

Ce projet fournit un assistant **local** pour Firefox qui peut:

- se connecter à un site via email/mot de passe depuis `.env`;
- demander le code 2FA dans le terminal puis l'injecter automatiquement;
- ouvrir les pages de sondage, lire les champs du formulaire et proposer des réponses;
- mémoriser les questions/réponses dans `database.db` pour réutiliser des réponses cohérentes sur des questions similaires.

## Limites d'usage

Ce dépôt est destiné à des usages **autorisés** (QA interne, formulaires de test, environnements de démonstration).
L'automatisation de plateformes de gains/sondages tiers (ex: domaines de type Coinpayu/offerwall) est bloquée par défaut dans le code.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Variables utilisées dans `.env`:

- `EMAIL`
- `PASSWORD`
- `LOGIN_URL`
- `SURVEY_URL`
- `MODEL_PATH`
- `MODEL_URL`
- `DB_PATH` (optionnel, défaut: `database.db`)
- `TWO_FA_CODE` (optionnel, pour injecter automatiquement le code OTP sans saisie terminal)
- `MAX_SURVEYS` (optionnel, défaut: `20`)

## Lancement

```bash
python3 bot.py
```

Le script demandera le code 2FA dans le terminal. Si la connexion automatique/2FA échoue, vous pouvez terminer manuellement dans Firefox puis confirmer en tapant `Y` dans le terminal.

Ensuite, il tentera d'ouvrir et de compléter les formulaires détectés (limite par défaut: 20 formulaires).

## Plan de travail recommandé (Coinpayu / pages similaires)

1. **Connexion** : le bot essaie plusieurs sélecteurs (`email`, `username`, `input[type='email']`, etc.) pour éviter les échecs si le formulaire change.
2. **2FA** : il lit d'abord `TWO_FA_CODE` / `OTP_CODE`, sinon il demande la saisie dans le terminal.
3. **Détection des sondages** : il recherche des liens/boutons contenant `survey`, `sondage`, `offerwall`, `lootably`, `bitlabs`, `cpx`.
4. **Réponse automatique** : il traite radio/checkbox/select/champs texte et tente de cliquer sur `suivant/continuer/submit/terminer`.
5. **Mémoire locale** : chaque question-réponse est stockée dans SQLite pour réutiliser des réponses cohérentes sur les prochaines sessions.

> Important: vérifiez les règles d'utilisation de la plateforme cible avant d'automatiser des actions.

## Mémoire locale des réponses

Les réponses sont enregistrées dans SQLite (`database.db`) avec:

- le texte de la question (original + normalisé),
- la réponse envoyée,
- le type de champ (texte, radio, checkbox, select),
- un timestamp.

Lorsqu'une question similaire est retrouvée, le bot réutilise la réponse précédente pour garder une cohérence entre sondages.
