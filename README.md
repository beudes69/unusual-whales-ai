# OKX AI PRO

OKX AI PRO est un assistant local d'aide à la décision pour les contrats
Futures `USDT-SWAP` d'OKX. Il est conçu pour fonctionner en continu, expliquer
chaque signal et ne jamais ouvrir de position automatiquement.

> État du projet : **phase 1 terminée — socle applicatif uniquement**. Aucun
> appel à OKX, signal de trading ou ordre n'est implémenté à ce stade.

## Principes

- aucune exécution automatique d'ordre ;
- aucune donnée inventée ;
- API gratuites et calculs locaux uniquement ;
- composants optionnels désactivables lorsque leur source gratuite est absente ;
- configuration explicite, logs rotatifs et erreurs contrôlées ;
- compatibilité ciblée : Python 3.13, Windows, Linux et Termux.

Le paquet reste compatible avec Python 3.11 et 3.12 pour faciliter le
développement, tandis que l'analyse statique et l'intégration continue ciblent
explicitement Python 3.13.

## Installation

Cloner le dépôt, puis créer un environnement virtuel.

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

### Termux

```bash
pkg update
pkg install python git
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Le socle n'a aucune dépendance d'exécution externe. Les outils installés par
`requirements-dev.txt` servent aux tests et aux contrôles de qualité.

## Configuration

Les valeurs sont appliquées dans cet ordre, de la moins prioritaire à la plus
prioritaire :

1. `src/okx_ai_pro/default.toml` ;
2. fichier TOML fourni avec `--config` ;
3. variables d'environnement `OKX_AI_PRO_*`.

Copier `config.example.toml` vers `config.local.toml` pour créer une
configuration locale. Ce dernier est ignoré par Git.

```bash
okx-ai-pro --config config.local.toml
# ou
python -m okx_ai_pro --config config.local.toml
```

Variables reconnues :

| Variable | Usage |
|---|---|
| `OKX_AI_PRO_ENVIRONMENT` | `development`, `testing` ou `production` |
| `OKX_AI_PRO_TIMEZONE` | Fuseau métier, `UTC` par défaut |
| `OKX_AI_PRO_DATA_DIRECTORY` | Répertoire des futures données locales |
| `OKX_AI_PRO_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `OKX_AI_PRO_LOG_CONSOLE_ENABLED` | Active les logs console |
| `OKX_AI_PRO_LOG_FILE_ENABLED` | Active les logs fichier |
| `OKX_AI_PRO_LOG_DIRECTORY` | Répertoire des logs |

Les booléens acceptent `true/false`, `yes/no`, `on/off` et `1/0`. Aucun secret
OKX n'est défini pendant cette phase.

## Qualité et tests

```bash
ruff check .
ruff format --check .
mypy
pytest --cov=okx_ai_pro --cov-report=term-missing
```

La couverture minimale est fixée à 90 %. La CI exécute ces contrôles sous
Python 3.13 sur Linux et Windows. Termux utilise le même code Python pur et les
mêmes chemins basés sur `pathlib`.

## Architecture actuelle

```text
.
├── src/okx_ai_pro/
│   ├── cli.py               # point d'entrée et validation du socle
│   ├── default.toml         # valeurs centralisées
│   ├── exceptions.py        # erreurs applicatives
│   ├── logging_config.py    # console et fichiers rotatifs UTC
│   └── settings.py          # chargement, priorité et validation
├── tests/                   # tests unitaires
├── docs/architecture.md     # règles d'architecture
├── config.example.toml
├── pyproject.toml
└── requirements*.txt
```

Les répertoires `data/` et `logs/` sont créés au premier démarrage et ignorés
par Git. Consulter [l'architecture](docs/architecture.md) pour les frontières
prévues entre les futures phases.

## Avertissement

Ce logiciel fournit une aide analytique et ne constitue pas un conseil
financier. Le trader reste seul responsable de ses décisions et de ses ordres.