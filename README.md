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

## Les 24 outils

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

### Traçabilité et reporting

| Outil | Rôle |
|---|---|
| `odoo_journal_start` | Ouvrir un journal d'intervention (titre + objectif) |
| `odoo_journal_chapter` | Ouvrir une étape de travail et sa justification métier |
| `odoo_journal_note` | Consigner une décision, une observation, une alerte |
| `odoo_journal_report` | Générer le rapport d'intervention (HTML et/ou Markdown) |
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
en PDF. Il contient la synthèse chiffrée, les volumes par modèle, le déroulé chronologique
par étape, et pour chaque modification le détail `avant → après`. Les suppressions y
apparaissent avec **le nom de ce qui a disparu** et un marquage « irréversible ».

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
