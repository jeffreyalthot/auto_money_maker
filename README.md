# Local Firefox Survey Assistant

Ce projet fournit un assistant **local** pour Firefox qui peut:

- se connecter à un site via email/mot de passe depuis `.env`;
- demander le code 2FA dans le terminal puis l'injecter automatiquement;
- ouvrir les pages de sondage, lire les champs du formulaire et proposer des réponses;
- mémoriser les questions/réponses dans `database.db` pour réutiliser des réponses cohérentes sur des questions similaires.

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

## Lancement

```bash
python3 bot.py
```

Le script demandera le code 2FA dans le terminal, puis tentera d'ouvrir et de compléter les sondages détectés.

## Mémoire locale des réponses

Les réponses sont enregistrées dans SQLite (`database.db`) avec:

- le texte de la question (original + normalisé),
- la réponse envoyée,
- le type de champ (texte, radio, checkbox, select),
- un timestamp.

Lorsqu'une question similaire est retrouvée, le bot réutilise la réponse précédente pour garder une cohérence entre sondages.
