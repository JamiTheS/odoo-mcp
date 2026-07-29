# odoo-mcp

Serveur [MCP](https://modelcontextprotocol.io) pour piloter n'importe quelle base **Odoo**
via XML-RPC. Fonctionne avec tout client MCP : **Claude Code, Antigravity, Gemini CLI,
Claude Desktop, Cursor...**

- **Aucun identifiant stocké** : l'assistant demande l'URL, le login et la clé API dans la
  conversation (`odoo_connect`) — ils ne vivent qu'en mémoire, le temps de la session.
- **Écriture bloquée par défaut** : elle s'active par un outil dédié (`odoo_enable_write`),
  que l'assistant ne doit appeler qu'après accord explicite de l'utilisateur.
- **Modifications de masse prévisualisées** : `odoo_update_where` montre d'abord combien
  d'enregistrements sont visés, avec un échantillon avant/après, et n'écrit qu'après
  confirmation.
- **Import/export Excel** : le serveur tourne en local, il lit et écrit les fichiers
  directement — un catalogue de 1 500 lignes s'importe sans passer par la conversation.
- **Écritures rejouables** : `odoo_upsert` et l'import par External ID mettent à jour au lieu
  de dupliquer.
- **Tout est tracé** : chaque écriture est journalisée automatiquement avec son état
  avant/après, et `odoo_journal_report` produit un rapport d'intervention présentable
  au client — ce qui a été fait, et pourquoi.
- **Tableaux de bord générés** : `odoo_dashboard_create` produit de vrais tableaux de bord
  Odoo dont les graphiques sont recalculés en direct, pas des captures d'écran.
- **Maquettes de démonstration sûres** : un questionnaire de qualification cadre l'avant-vente,
  et le mode démonstration neutralise **toute** adresse e-mail écrite — aucune fausse facture
  ne peut partir chez une vraie entreprise.

## Installation

Prérequis : [uv](https://docs.astral.sh/uv/getting-started/installation/) —
`winget install astral-sh.uv` (Windows) / `brew install uv` (macOS) /
`curl -LsSf https://astral.sh/uv/install.sh | sh` (Linux).

### Claude Code

```bash
claude mcp add --scope user odoo -- uvx --from git+https://github.com/JamiTheS/odoo-mcp odoo-mcp
```

### Antigravity / Gemini CLI

Dans la configuration MCP (Antigravity : panneau **MCP Servers** → *Manage MCP config* ;
Gemini CLI : `~/.gemini/settings.json`) :

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/JamiTheS/odoo-mcp", "odoo-mcp"]
    }
  }
}
```

### Claude Desktop

Paramètres → Développeur → `claude_desktop_config.json` : même bloc JSON que ci-dessus.

C'est tout : `uvx` télécharge, installe et lance le serveur tout seul au premier démarrage,
et le met en cache ensuite.

## Utilisation

Au premier échange, l'assistant demande trois informations :

1. **URL de la base** — ex. `https://acme.odoo.com`
2. **Login** — l'e-mail de connexion
3. **Clé API** — dans Odoo : avatar → *Mon profil* → *Sécurité du compte* →
   *Nouvelle clé API*, en **laissant le champ « Scope » vide** (une clé de scope « MCP »
   est refusée en XML-RPC)

Puis on parle à sa base en langage naturel : *« combien de commandes en cours ? »*,
*« montre les champs de res.partner »*, *« corrige le téléphone de ce contact »*.

### Base fixe (optionnel)

Pour se connecter automatiquement à une base donnée, ajouter à la déclaration du serveur
(la clé est alors en clair dans le fichier de config du client — à réserver aux bases
de test) :

```json
"env": {
  "ODOO_URL": "https://acme.odoo.com",
  "ODOO_USERNAME": "vous@acme.com",
  "ODOO_API_KEY": "votre-clé"
}
```

## Les 32 outils

### Connexion

| Outil | Rôle |
|---|---|
| `odoo_connect` | Se connecter (URL + login + clé API) — en mémoire seulement |
| `odoo_status` | État de la connexion, version du serveur, mode |
| `odoo_enable_write` | Activer/couper l'écriture pour la session |

### Lecture

| Outil | Rôle |
|---|---|
| `odoo_models` | Lister les modèles |
| `odoo_fields` | Décrire les champs d'un modèle (option `writable_only`) |
| `odoo_search` | Recherche + lecture (domaine, tri, pagination) |
| `odoo_count` | Comptage |
| `odoo_read` | Lecture par identifiants |
| `odoo_name_find` | Retrouver un enregistrement par son nom (« le client Polytec » → id) |
| `odoo_aggregate` | Comptage et sommes par groupe, avec granularité de date (`date_order:month`) |

### Écriture *(exige `odoo_enable_write`)*

| Outil | Rôle |
|---|---|
| `odoo_create` | Création |
| `odoo_write` | Modification par identifiants |
| `odoo_update_where` | Modification de masse sur un domaine, **prévisualisée** avant application |
| `odoo_unlink` | Suppression définitive (confirmation exigée au-delà de 50) |
| `odoo_upsert` | Créer-ou-mettre-à-jour par External ID |
| `odoo_execute` | Appel brut `execute_kw` (même garde-fou sur les méthodes d'écriture) |

### Fichiers

| Outil | Rôle |
|---|---|
| `odoo_import_file` | Importer un .xlsx/.csv — modes `inspect`, `check`, `run` |
| `odoo_export_file` | Exporter une recherche vers .xlsx ou .csv |
| `odoo_get_attachment` | Télécharger une pièce jointe (PDF de facture, document...) |

### Démonstration et avant-vente

| Outil | Rôle |
|---|---|
| `odoo_demo_questionnaire` | Questionnaire de qualification à faire remplir avant une démo |
| `odoo_demo_mode` | Filet de sécurité : neutralise toute adresse e-mail écrite |
| `odoo_demo_check` | Audite la base et corrige les adresses réelles restantes |

### Tableaux de bord

| Outil | Rôle |
|---|---|
| `odoo_dashboard_list` | Inventaire des tableaux de bord et de leurs rubriques |
| `odoo_dashboard_inspect` | Décrire en clair le contenu d'un tableau de bord |
| `odoo_dashboard_create` | Créer un tableau de bord de graphiques calculés en direct |
| `odoo_saved_analysis` | Enregistrer une analyse réutilisable (« favori » Odoo) |

### Traçabilité et reporting

| Outil | Rôle |
|---|---|
| `odoo_journal_start` | Ouvrir un journal d'intervention (titre + objectif) |
| `odoo_journal_chapter` | Ouvrir une étape de travail et sa justification métier |
| `odoo_journal_note` | Consigner une décision, une observation, une alerte |
| `odoo_journal_report` | Générer le rapport d'intervention (HTML et/ou Markdown) |
| `odoo_presentation_guide` | Générer le déroulé de démonstration à suivre en réunion client |
| `odoo_recent_changes` | Ce qui a bougé récemment, d'après l'audit natif d'Odoo |

## Le rapport d'intervention

Quand c'est l'assistant qui construit un flux entier, retrouver après coup ce qui a été fait
— et l'expliquer à un client — devient vite impossible à partir du seul historique de
conversation. Le serveur étant le point de passage obligé de toute écriture, il journalise
tout automatiquement.

```
odoo_journal_start("Maquette Pycarelle", "Traduire le flux affaires dans Odoo")
odoo_journal_chapter("Référentiel articles", "Aucun catalogue n'existait : prérequis
                                              pour bloquer les achats hors contrat")
   → les écritures suivantes sont tracées, avec leur état avant/après
odoo_journal_note("Le stock client reste hors périmètre (décision du 22/07)", "decision")
odoo_journal_report(format="both")
```

Le rapport HTML est autonome (aucune ressource externe), présentable tel quel ou imprimable
en PDF. Il contient la synthèse chiffrée, les volumes par type d'information, le déroulé
chronologique par étape, et pour chaque modification le détail `avant → après`. Les
suppressions y apparaissent avec **le nom de ce qui a disparu** et un marquage
« irréversible ».

**Le rapport parle français, pas Odoo.** Les noms techniques sont traduits en langage
courant — `res.partner` devient « Contacts (clients, fournisseurs) », `sale.order` devient
« Devis et commandes clients » — et chaque type d'information est accompagné de l'endroit où
le trouver dans l'interface. Un dirigeant qui n'a jamais ouvert Odoo comprend le document.

## Le guide de présentation

`odoo_presentation_guide` produit le **déroulé à suivre en réunion client**, écran par écran,
déduit de ce qui a réellement été fait : seules les étapes correspondant aux données mises
en place apparaissent, dans l'ordre naturel du métier (contacts → catalogue → devis →
livraison → facture → rentabilité).

Chaque étape donne le chemin de menu exact, les clics à faire sous forme de **cases à
cocher**, les enregistrements précis à ouvrir, et une phrase d'accroche à dire au client.
Le fichier HTML s'ouvre pendant la réunion : on coche au fur et à mesure, on n'oublie
aucune étape, et on garde le fil du discours.

```
### Étape 3 — Du devis à la commande client

Où aller : Ventes → Commandes → Devis

[ ] Ouvrir un devis de la démonstration
[ ] Montrer les lignes : articles, quantités, prix
[ ] Expliquer le bouton « Confirmer » : le devis devient une commande ferme

> À dire : C'est le point de bascule — un clic sur « Confirmer », et le reste
  de la chaîne se met en route tout seul.
```

Les journaux sont écrits en JSONL dans `~/odoo-mcp-journaux/` (une ligne par opération,
lisible et diffable), et un rapport peut être regénéré plus tard à partir d'un journal
ancien via `journal_path`.

`odoo_recent_changes` complète le dispositif : il interroge les champs d'audit d'Odoo
(`write_date`, `write_uid`), donc il voit aussi les modifications faites directement dans
l'interface par d'autres personnes.

## Importer un fichier

Trois modes à enchaîner, qui évitent d'écrire n'importe quoi dans la base :

1. **`inspect`** — structure du fichier : colonnes, taux de remplissage, valeurs distinctes,
   doublons d'identifiant. Aucune connexion nécessaire.
2. **`check`** — construit les lignes et vérifie chaque champ contre le modèle, sans rien
   écrire.
3. **`run`** — importe par lots via `load()`, l'import natif d'Odoo.

Le `mapping` relie les colonnes du fichier aux champs Odoo. Il est indispensable : les
en-têtes des fichiers exportés depuis Odoo sont des libellés d'interface (`Name*`,
`Sales Price`), jamais des noms de champs.

```json
{
  "_columns": {
    "Code":  "id",
    "Nom":   "name",
    "Pays":  "country_id/id",
    "Notes": null
  },
  "_constants": { "is_company": "True" },
  "_replace":   { "type": { "Goods": "consu" } }
}
```

Mapper une colonne sur `id` (External ID) rend l'import **rejouable** : une seconde exécution
met à jour au lieu de dupliquer. Et `load()` rejette un lot entier en cas d'erreur — un échec
ne laisse jamais de données à moitié écrites.

## Préparer une démonstration

Deux usages coexistent dans cet outil. Le **consultant** intervient sur des données
réelles : il lui faut de la traçabilité, d'où le journal et le rapport. L'**avant-vente**
construit des données fictives pour un prospect : il lui faut de la vraisemblance, vite.

Pour ce second cas, `odoo_demo_questionnaire` fournit une trame de qualification en huit
sections — le métier, ce qu'il vend, ses achats, son pilotage, sa facturation, **les
spécificités venant de ses propres clients**, son vocabulaire maison, et le problème qu'il
cherche à résoudre. C'est ce dernier point qui fait la différence entre une démonstration
générique et une démonstration où le prospect se reconnaît.

La composition de la maquette n'est pas codée dans le serveur : c'est l'assistant qui
l'écrit à partir des réponses. Aucun catalogue figé ne produira des noms d'articles et un
vocabulaire aussi justes qu'un modèle de langage — et surtout, cela fonctionne pour un
métier qu'on n'avait pas prévu.

### Le filet e-mail, non négociable

Une base de démonstration n'est presque jamais neutralisée : Odoo y envoie de vrais
courriels dès qu'on confirme une commande ou une facture. Une adresse réelle dans un jeu
fictif, et une vraie entreprise reçoit une fausse facture.

```
odoo_demo_mode(actif=true)
```

Une fois activé, **toute** adresse écrite est réécrite vers `example.com` — domaine
réservé par la RFC 2606, qui ne peut appartenir à personne. La garantie est posée au seul
endroit par lequel passent toutes les écritures, elle tient donc quel que soit l'outil
utilisé : création, modification, upsert, import de fichier, ou même appel brut
`odoo_execute`. La partie gauche de l'adresse est conservée, donc `jean.dupont@example.com`
reste lisible à l'écran pendant la démonstration.

`odoo_demo_check` balaie une base reprise de quelqu'un d'autre et signale — ou corrige —
les adresses qui pourraient encore recevoir du courrier.

## Générer un tableau de bord

Les graphiques produits sont **recalculés par Odoo à chaque ouverture** — ce ne sont ni
des images ni des valeurs figées. Chacun porte sa source, son regroupement, sa mesure et
son filtre :

```json
[
  {"titre": "Chiffre d'affaires par mois", "model": "sale.order",
   "groupby": ["date_order:month"], "mesure": "amount_untaxed",
   "type": "line", "domaine": [["state","=","sale"]], "pleine_largeur": true},
  {"titre": "Commandes par vendeur", "model": "sale.order",
   "groupby": ["user_id"], "mesure": "__count", "type": "bar"}
]
```

Trois types : `bar` (comparer), `line` (suivre dans le temps), `pie` (répartition). Sur
un champ date, la granularité s'écrit `champ:month` — aussi `day`, `week`, `quarter`,
`year`. `__count` compte au lieu de sommer.

Le modèle, les champs de regroupement et le type de la mesure sont **vérifiés avant
écriture**, et la vérification fonctionne en lecture seule : on peut valider une maquette
avant de demander l'autorisation d'écrire. C'est important, car un tableau de bord qui
référence un champ inexistant s'ouvre vide, sans message d'erreur.

**[SPREADSHEETS.md](SPREADSHEETS.md) explique en détail** comment fonctionnent les
tableurs Odoo : le format o-spreadsheet, les quatre façons de connecter des données et
leur robustesse respective, le choix de la bonne source (`sale.report` plutôt que
`sale.order`…), et ce que ce connecteur ne génère volontairement pas.

## Économie de contexte

Le serveur régule lui-même ce qu'il renvoie, sans perdre l'accès à la donnée.

**Il rogne, il ne refuse jamais.** Une réponse trop volumineuse est réduite
progressivement ; les lignes rendues restent complètes, les métadonnées sont préservées,
et la réponse indique toujours combien de lignes sur combien et l'`offset` pour la suite.
Une troncature silencieuse mènerait à des conclusions fausses — c'est pire qu'une réponse
longue.

**Il dépose l'intégralité sur disque.** Quand un résultat dépasse le plafond, le jeu
complet est écrit dans `~/odoo-mcp-resultats/` et le chemin est renvoyé. L'assistant le
relit avec son propre outil de lecture, sans repasser par Odoo : quelques dizaines de
tokens au lieu de dizaines de milliers.

**Il se resserre à mesure que la session avance.**

| Palier | Déclenché à | Plafond | Champs par défaut | Limite par défaut |
|---|---|---|---|---|
| confort | départ | 60 k car | 12 | 50 |
| économie | 200 k rendus | 30 k | 8 | 30 |
| strict | 500 k | 15 k | 6 | 20 |

**Les défauts sont sobres.** `odoo_search` sans `fields` choisissait autrefois *tous* les
champs — 169 000 tokens pour dix contacts. Il retient désormais une douzaine de champs
utiles et écarte les types lourds. `odoo_fields` renvoie une forme compacte
(`"partner_id":"many2one>res.partner*"`) au lieu de 20 000 tokens de détail.

L'état de consommation est visible à tout moment dans `odoo_status`.

Si le coût permanent des définitions d'outils vous gêne, le levier restant est de
désactiver dans votre client MCP ceux dont vous ne vous servez pas.

## Notes de terrain

- Inspecter les champs (`odoo_fields`) **avant** d'écrire : les noms changent entre versions
  d'Odoo (en 19, `is_company` existe mais pas `company_type` ; `groups_id` est devenu
  `group_ids` ; le contenu d'une pièce jointe est passé de `datas` à `raw`).
- Préférer `odoo_upsert` à `odoo_create` pour toute donnée de maquette ou d'import.
- Un échec d'écriture sur un champ non fourni (ex. `credit_limit` en créant un contact) est
  un problème de **droits Odoo**, pas de données.
- Les relations one2many (ex. `seller_ids`) **s'accumulent** à chaque écriture au lieu de se
  remplacer — purger avant de rejouer un chargement.
- Archiver (`{"active": false}`) est presque toujours préférable à supprimer : Odoo n'a pas
  de corbeille.

## Licence

MIT
