# OKX AI PRO

OKX AI PRO est un assistant local d'aide à la décision pour les contrats
Futures `USDT-SWAP` d'OKX. Il est conçu pour fonctionner en continu, expliquer
chaque signal et ne jamais ouvrir de position automatiquement.

> État du projet : **phase 1 validée ; phase 2 en développement**. Aucun signal,
> stratégie ou ordre n'est implémenté.

## Principes

- aucune exécution automatique d'ordre ;
- aucune donnée inventée ;
- API gratuites et calculs locaux uniquement ;
- composants optionnels désactivables lorsque leur source gratuite est absente ;
- configuration explicite, logs rotatifs et erreurs contrôlées ;
- compatibilité ciblée : Python 3.13, Windows, Linux et Termux.

Le projet utilise `uv` pour verrouiller et synchroniser ses dépendances.
L'analyse statique et l'intégration continue ciblent Python 3.13.

## Installation

Installer [`uv`](https://docs.astral.sh/uv/), cloner le dépôt puis synchroniser
l'environnement verrouillé.

### Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --locked
uv run python main.py
```

### Windows PowerShell

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync --locked
uv run python main.py
```

### Termux

```bash
pkg update
pkg install python git uv
uv sync --locked
uv run python main.py
```

Après activation de l'environnement (`source .venv/bin/activate` sous
Linux/Termux), le démarrage direct demandé est également disponible :

```bash
python main.py
```

`requirements*.txt` reste fourni comme export de compatibilité ; `uv.lock` est
la source reproductible utilisée par le projet.

## Configuration

Les valeurs sont appliquées dans cet ordre, de la moins prioritaire à la plus
prioritaire :

1. `src/okx_ai_pro/default.toml` ;
2. fichier TOML fourni avec `--config` ;
3. variables d'environnement `OKX_AI_PRO_*`, validées par
   `pydantic-settings`.

Copier `config.example.toml` vers `config.local.toml` pour créer une
configuration locale. Ce dernier est ignoré par Git.

```bash
okx-ai-pro --config config.local.toml
# ou
python -m okx_ai_pro --config config.local.toml
```

Les variables imbriquées utilisent `__`, par exemple
`OKX_AI_PRO_OKX__REQUEST_TIMEOUT_SECONDS=5`. Les alias historiques suivants
restent reconnus :

| Variable | Usage |
|---|---|
| `OKX_AI_PRO_ENVIRONMENT` | `development`, `testing` ou `production` |
| `OKX_AI_PRO_TIMEZONE` | Fuseau métier, `UTC` par défaut |
| `OKX_AI_PRO_DATA_DIRECTORY` | Répertoire des futures données locales |
| `OKX_AI_PRO_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `OKX_AI_PRO_LOG_CONSOLE_ENABLED` | Active les logs console |
| `OKX_AI_PRO_LOG_FILE_ENABLED` | Active les logs fichier |
| `OKX_AI_PRO_LOG_DIRECTORY` | Répertoire des logs |

Les booléens acceptent `true/false`, `yes/no`, `on/off` et `1/0`. Les URL,
timeouts, politiques de retry, reconnexion et limites OKX sont centralisés dans
le TOML. Aucun secret OKX n'est nécessaire pour les données publiques.

## Qualité et tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=okx_ai_pro --cov-report=term-missing
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