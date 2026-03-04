# Local Firefox Survey Assistant (Safe Mode)

Ce projet fournit un assistant **local** pour Firefox qui peut:

- se connecter à un site via email/mot de passe depuis `.env`;
- demander le code 2FA dans le terminal puis l'injecter automatiquement;
- ouvrir les pages de sondage une par une;
- journaliser un compteur `share` / `bad` (selon validation manuelle).

## Important

Le script est volontairement en **mode sûr**:

- il **n'automatise pas** les réponses aux sondages;
- il nécessite une validation humaine pour rester conforme aux conditions d'utilisation des plateformes.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Un fichier `.env` est fourni avec les variables demandées.

Variables utilisées:

- `EMAIL`
- `PASSWORD`
- `LOGIN_URL`
- `SURVEY_URL`
- `MODEL_PATH`
- `MODEL_URL`

## Lancement

```bash
python3 bot.py
```

Le script demandera le code 2FA dans le terminal.

## Modèle IA local léger

Au démarrage, le script télécharge automatiquement un petit modèle local si absent (`MODEL_URL` -> `MODEL_PATH`).
Ce modèle n'est pas utilisé pour contourner des protections, uniquement pour de futures extensions locales.
