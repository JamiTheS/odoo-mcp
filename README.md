# odoo-mcp

Serveur [MCP](https://modelcontextprotocol.io) pour piloter n'importe quelle base **Odoo**
via XML-RPC. Fonctionne avec tout client MCP : **Claude Code, Antigravity, Gemini CLI,
Claude Desktop, Cursor...**

- **Aucun identifiant stocké** : l'assistant demande l'URL, le login et la clé API dans la
  conversation (`odoo_connect`) — ils ne vivent qu'en mémoire, le temps de la session.
- **Écriture bloquée par défaut** : elle s'active par un outil dédié (`odoo_enable_write`),
  que l'assistant ne doit appeler qu'après accord explicite de l'utilisateur.
- **Écritures rejouables** : `odoo_upsert` crée ou met à jour par External ID, sans doublon.

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

## Les 14 outils

| Outil | Rôle |
|---|---|
| `odoo_connect` | Se connecter (URL + login + clé API) — en mémoire seulement |
| `odoo_status` | État de la connexion, version du serveur, mode |
| `odoo_enable_write` | Activer/couper l'écriture pour la session |
| `odoo_models` | Lister les modèles |
| `odoo_fields` | Décrire les champs d'un modèle (option `writable_only`) |
| `odoo_search` | Recherche + lecture (domaine, tri, pagination) |
| `odoo_count` | Comptage |
| `odoo_read` | Lecture par identifiants |
| `odoo_aggregate` | Comptage/somme par groupe (remplace `read_group`, retiré du RPC en Odoo 18+) |
| `odoo_create` | Création *(écriture requise)* |
| `odoo_write` | Modification *(écriture requise)* |
| `odoo_unlink` | Suppression définitive *(écriture requise)* |
| `odoo_upsert` | Créer-ou-mettre-à-jour par External ID *(écriture requise)* |
| `odoo_execute` | Appel brut `execute_kw` (même garde-fou sur les méthodes d'écriture) |

## Notes de terrain

- Inspecter les champs (`odoo_fields`) **avant** d'écrire : les noms changent entre versions
  d'Odoo (en 19, `is_company` existe mais pas `company_type` ; `groups_id` est devenu
  `group_ids`).
- Préférer `odoo_upsert` à `odoo_create` pour toute donnée de maquette ou d'import.
- Un échec d'écriture sur un champ non fourni (ex. `credit_limit` en créant un contact) est
  un problème de **droits Odoo**, pas de données.
- Les relations one2many (ex. `seller_ids`) **s'accumulent** à chaque écriture au lieu de se
  remplacer — purger avant de rejouer un chargement.

## Licence

MIT
