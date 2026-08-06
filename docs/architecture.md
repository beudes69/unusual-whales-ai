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

## Périmètre de la phase 2

La phase 2 ajoute exclusivement le moteur de communication public OKX :

```text
Couche appelante
      ↓ dépend de
OkxClientProtocol / OkxClient
      ├── RestClientProtocol       → RestClient (httpx)
      └── WebSocketClientProtocol  → WebSocketClient (websockets)
              ├── ConnectionManagerProtocol
              └── RateLimiterProtocol
```

`OkxClient` est l'unique façade autorisée pour les futures couches métier.
Ses constructeurs acceptent des interfaces, ce qui permet de remplacer un
transport, un limiteur, une politique de retry ou un gestionnaire de connexion
sans modifier les consommateurs.

Le pipeline REST applique systématiquement :

```text
validation requête → quota local → appel httpx → classification erreur
                  → retry transitoire → validation Pydantic → modèle immuable
```

Les statuts 429/5xx, timeouts, erreurs réseau, codes fonctionnels et données
invalides ont des exceptions distinctes. Seuls les timeouts, indisponibilités,
erreurs réseau et limitations sont réessayés.

Le WebSocket possède une tâche de réception unique. Un silence déclenche le
heartbeat texte `ping`/`pong` attendu par OKX. Une coupure ferme l'ancien
transport, applique un backoff borné, recrée la connexion puis restaure chaque
abonnement enregistré. Chaque commande porte un identifiant et un abonnement
n'est actif qu'après acknowledgement. Les commandes dépassant la taille
configurée sont refusées localement. Une trame invalide ou l'échec d'un
callback est isolé sans arrêter la boucle ; une notice de maintenance provoque
une reconnexion anticipée.

Les URL, timeouts, quotas, délais et multiplicateurs sont configurés dans TOML.
Les chemins d'API et noms de champs restent des contrats de protocole versionnés
dans le code.

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
- dépendances de phase 2 verrouillées par `uv` pour chaque plateforme ;
- encodage explicite pour les fichiers ;
- tests CI Python 3.13 sous Linux et Windows ;
- code Python pur utilisable dans Termux.

Chaque nouvelle phase devra conserver ces contraintes et ajouter ses tests
sans modifier les frontières des couches déjà validées, sauf nécessité
documentée.
