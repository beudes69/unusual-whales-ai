# Architecture d'OKX AI PRO

## Périmètre de la phase 1

Cette phase fournit uniquement l'infrastructure commune :

- paquet Python installable selon une disposition `src/` ;
- configuration immuable, typée et validée par `pydantic-settings` ;
- surcharge TOML puis variables d'environnement ;
- chemins portables avec `pathlib` ;
- journalisation UTF-8, UTC et rotation locale ;
- hiérarchie d'exceptions contrôlées ;
- point d'entrée de validation ;
- tests, couverture, lint et analyse statique.

Elle ne contient volontairement aucun client réseau, modèle de marché,
indicateur, score ou mécanisme d'ordre.

## Règles de dépendance

Les futures fonctions seront ajoutées sous forme de composants indépendants.
Les couches externes dépendront des couches internes, jamais l'inverse :

```text
Interfaces (dashboard, Telegram, CLI)
                    ↓
Décision, veto, risque et recommandation de levier
                    ↓
Score et analyse multi-timeframe
                    ↓
Indicateurs et modèles de domaine
                    ↓
Collecte OKX et stockage
                    ↓
Infrastructure (configuration, logs, erreurs)
```

Les types du domaine ne devront pas dépendre du transport REST/WebSocket ni de
SQLite. Des interfaces (`Protocol`) isoleront les fournisseurs de données, ce
qui permettra de désactiver une source gratuite indisponible sans casser les
autres analyses.

## Configuration

`Settings` est un agrégat `BaseSettings` immuable. Le chargement suit une
priorité déterministe :

```text
valeurs intégrées < fichier TOML utilisateur < environnement
```

Les clés inconnues et les valeurs invalides provoquent une
`ConfigurationError` au démarrage. Cette stratégie empêche une faute de frappe
de lancer le service avec une valeur silencieusement ignorée. Le chargement ne
crée aucun fichier ; `prepare_runtime_directories()` matérialise seulement les
répertoires nécessaires.

Les secrets futurs ne devront être ni stockés dans TOML versionné, ni écrits
dans les logs.

## Journalisation

Tous les composants utilisent la hiérarchie `okx_ai_pro.*`. Les dates sont en
UTC et les fichiers en UTF-8. La rotation borne l'espace disque utilisé. Une
reconfiguration ferme les anciens handlers afin d'éviter les doublons et les
descripteurs de fichiers abandonnés, notamment sous Windows.

## Portabilité

- aucune concaténation manuelle de chemin ;
- aucune commande système dépendante d'un shell ;
- aucune dépendance binaire en phase 1 ;
- encodage explicite pour les fichiers ;
- tests CI Python 3.13 sous Linux et Windows ;
- code Python pur utilisable dans Termux.

Chaque nouvelle phase devra conserver ces contraintes et ajouter ses tests
sans modifier les frontières des couches déjà validées, sauf nécessité
documentée.
