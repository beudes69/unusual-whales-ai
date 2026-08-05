# Moteur de communication OKX

## Frontière publique

Les consommateurs utilisent `OkxClient` ou `OkxClientProtocol`. Les classes
`RestClient` et `WebSocketClient` sont des détails d'infrastructure injectables.
Cette règle interdit aux futures analyses de dépendre de `httpx`, de
`websockets` ou des enveloppes JSON brutes d'OKX.

Le moteur couvre uniquement les données publiques API V5. Il ne contient
aucune authentification et aucun endpoint d'ordre.

## REST

| Méthode | Endpoint OKX V5 | Résultat |
|---|---|---|
| `get_usdt_swap_contracts()` | `/api/v5/public/instruments` | contrats `SWAP` réglés en USDT |
| `get_contract(id)` | `/api/v5/public/instruments` | métadonnées d'un contrat |
| `get_candles(id, ...)` | `/api/v5/market/candles` | bougies OHLCV validées |
| `get_funding_rate(id)` | `/api/v5/public/funding-rate` | funding courant |
| `get_open_interest(id)` | `/api/v5/public/open-interest` | open interest |
| `get_order_book(id, ...)` | `/api/v5/market/books` | instantané du carnet |
| `get_mark_price(id)` | `/api/v5/public/mark-price` | mark price |
| `get_index_price(index_id)` | `/api/v5/market/index-tickers` | prix de l'indice |

`get_index_price` attend l'identifiant d'indice, par exemple `BTC-USDT`, et non
le contrat `BTC-USDT-SWAP`. La propriété `Instrument.underlying` fournit cette
valeur lorsqu'OKX la renvoie.

Les limites de profondeur et de pagination sont validées avant le réseau.
Chaque réponse traverse une enveloppe contrôlée (`code`, `msg`, `data`) puis un
modèle Pydantic. Une réponse vide ou ambiguë n'est jamais remplacée par une
valeur inventée.

## WebSocket

Un abonnement est une valeur immuable :

```python
from okx_ai_pro.okx import WebSocketSubscription

subscription = WebSocketSubscription(
    channel="mark-price",
    instrument_id="BTC-USDT-SWAP",
)
```

Il peut être enregistré avant ou après `OkxClient.start()`. Plusieurs callbacks
et plusieurs abonnements peuvent coexister. Le premier callback déclenche
`subscribe`; le dernier retrait déclenche `unsubscribe`.

La boucle maintient les invariants suivants :

- un seul lecteur appelle `recv()` ;
- heartbeat texte après une période sans données ;
- fermeture contrôlée de l'ancienne connexion ;
- backoff de reconnexion borné ;
- restauration des abonnements après chaque reconnexion ;
- trames invalides et callbacks défaillants isolés ;
- arrêt idempotent annulant toute reconnexion future.

## Erreurs

Toutes les erreurs réseau exposées dérivent de `OkxCommunicationError`.

| Exception | Signification |
|---|---|
| `OkxTimeoutError` | délai d'ouverture, REST ou heartbeat dépassé |
| `OkxNetworkError` | transport HTTP inaccessible |
| `OkxApiUnavailableError` | API indisponible ou erreur serveur |
| `OkxHttpError` | statut HTTP non récupérable |
| `OkxApiError` | code fonctionnel OKX non nul |
| `OkxRateLimitError` | quota distant atteint |
| `OkxInvalidDataError` | JSON ou schéma de réponse invalide |
| `OkxRequestError` | paramètres locaux invalides |
| `OkxWebSocketClosedError` | canal fermé ou heartbeat perdu |
| `OkxSubscriptionError` | abonnement refusé par OKX |

Le retry est limité aux erreurs transitoires. Une erreur de requête ou de
parsing ne doit jamais être répétée automatiquement.

## Tests

La suite n'utilise pas Internet :

- `httpx.MockTransport` simule statuts, timeouts et enveloppes REST ;
- des connexions asynchrones en mémoire simulent messages et coupures ;
- une horloge injectable teste les quotas sans attente réelle ;
- les callbacks, reconnexions et réabonnements sont vérifiés
  déterministiquement.

Référence du protocole :
[documentation officielle OKX API V5](https://www.okx.com/docs-v5/en/).
