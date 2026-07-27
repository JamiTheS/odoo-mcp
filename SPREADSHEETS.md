# Comprendre les tableurs et tableaux de bord Odoo

Note de terrain rédigée en disséquant les 14 tableaux de bord livrés avec Odoo 19.
Objectif : savoir ce qu'on peut générer de façon fiable, et ce qu'il vaut mieux faire à
la main dans l'interface.

## Ce qu'est réellement un tableau de bord Odoo

Ce n'est ni une page web ni une vue Odoo classique : c'est un **classeur**, au format
open source *o-spreadsheet*, stocké en JSON encodé en base64.

| Objet | Rôle | Où le voir |
|---|---|---|
| `spreadsheet.dashboard` | Un tableau de bord | Menu « Tableaux de bord » |
| `spreadsheet.dashboard.group` | Une rubrique du menu | Onglets du menu |
| `documents.document` | Un tableur libre (hors dashboards) | Application Documents |
| `spreadsheet.revision` | Historique collaboratif | — |

Le contenu vit dans le champ `spreadsheet_binary_data`. Il est **modifiable par API**,
ce qui rend la génération possible.

## Anatomie du JSON

```
{
  "version": "18.5.10",
  "sheets": [ { "id", "name", "cells", "styles", "figures": [...] } ],
  "styles" / "formats" / "borders",     // bibliothèques de mise en forme
  "pivots": {},                          // tableaux croisés déclarés
  "lists": {},                           // listes d'enregistrements
  "globalFilters": [],                   // filtres en haut du tableau de bord
  "settings": { "locale": {...} }        // séparateurs, format de date
}
```

Tout l'intérêt est dans `sheets[].figures` : chaque figure est un graphique posé sur la
feuille, avec sa position (`col`, `row`, `offset`) et sa taille en pixels.

## Les quatre façons de connecter des données

C'est le point à comprendre, parce que les quatre n'ont pas du tout la même robustesse.

### 1. Le graphique Odoo — autonome et fiable

Type `odoo_bar`, `odoo_line`, `odoo_pie`, `odoo_combo`. Il porte **toute** sa définition :

```json
"metaData": { "resModel": "sale.order", "groupBy": ["date_order:month"],
              "measure": "amount_untaxed", "mode": "line" },
"searchParams": { "domain": [["state","=","sale"]], "groupBy": [...] }
```

Odoo interroge la base à chaque ouverture. Aucune formule, aucune valeur figée, rien à
rafraîchir. **C'est ce que génère `odoo_dashboard_create`**, et c'est le seul mécanisme
que je recommande pour de la génération automatique.

### 2. Le tableau croisé (`pivots`) — puissant mais couplé aux cellules

Un pivot est déclaré une fois, puis **appelé depuis les cellules** par des formules
`=ODOO.PIVOT(...)`. La déclaration et les formules doivent rester cohérentes : décaler
une ligne casse l'ensemble. Aucun des tableaux de bord standard d'Odoo n'en utilise —
signe que ce n'est pas le mécanisme privilégié.

### 3. L'indicateur chiffré (`scorecard`) — trompeusement simple

La tuile ne contient pas de valeur : elle pointe vers une cellule (`"keyValue": "Data!C2"`).
Dans les tableaux de bord Odoo, cette cellule renvoie vers une autre, qui contient une
formule comptable (`ODOO.BALANCE`, `ODOO.CREDIT`…). Une chaîne de trois ou quatre
renvois, spécifique à la comptabilité.

**Volontairement non généré par ce connecteur** : trop fragile, et un histogramme à une
seule barre transmet la même information. Si un indicateur chiffré est indispensable,
mieux vaut le créer à la main dans l'interface.

### 4. La liste (`lists`) — un tableau d'enregistrements

Même logique que le pivot : déclaration + formules `=ODOO.LIST(...)` dans les cellules.
Utile pour un extrait de commandes récentes, mais même fragilité.

## Ce que fait le connecteur

| Outil | Ce qu'il produit |
|---|---|
| `odoo_dashboard_list` | Inventaire des tableaux de bord et rubriques |
| `odoo_dashboard_inspect` | Traduction en clair d'un tableau de bord existant |
| `odoo_dashboard_create` | Un tableau de bord de graphiques Odoo (données en direct) |
| `odoo_saved_analysis` | Un « favori » réutilisable dans les vues Odoo |

### Générer un tableau de bord

```json
[
  {"titre": "Chiffre d'affaires par mois", "model": "sale.order",
   "groupby": ["date_order:month"], "mesure": "amount_untaxed",
   "type": "line", "domaine": [["state","=","sale"]], "pleine_largeur": true},

  {"titre": "Commandes par vendeur", "model": "sale.order",
   "groupby": ["user_id"], "mesure": "__count", "type": "bar"},

  {"titre": "Répartition par pays", "model": "res.partner",
   "groupby": ["country_id"], "mesure": "__count", "type": "pie"}
]
```

Trois types : `bar` (comparer), `line` (suivre dans le temps), `pie` (répartition).
Sur un champ date, la granularité s'écrit `champ:month` — aussi `day`, `week`,
`quarter`, `year`. `__count` compte les enregistrements au lieu de sommer un champ.

Tout est vérifié avant écriture : modèle existant, champs de regroupement valides,
mesure numérique. C'est important, parce qu'**un tableau de bord qui référence un champ
inexistant s'ouvre vide, sans le moindre message d'erreur** — le genre de panne qui fait
perdre une heure.

### L'alternative légère : l'analyse sauvegardée

`odoo_saved_analysis` crée un favori (`ir.filters`) : le client le retrouve dans le menu
« Favoris » de la vue concernée, avec regroupement et mesures déjà en place. Pour une
analyse ponctuelle qu'il voudra rejouer lui-même, c'est plus adapté qu'un tableau de bord.

## Choisir la bonne source de données

Le réflexe qui change tout : pour une analyse, Odoo fournit souvent un **modèle de
rapport** dédié, plus riche que le modèle transactionnel.

| Sujet | Modèle brut | Modèle d'analyse (préférable) |
|---|---|---|
| Ventes | `sale.order` | `sale.report` |
| Facturation | `account.move` | `account.invoice.report` |
| Achats | `purchase.order` | `purchase.report` |
| Stock | `stock.move` | `stock.move.line` |
| Projet | `project.task` | `project.task` (+ analytique) |

Les modèles en `.report` sont dénormalisés : une ligne par ligne de commande, avec le
client, le vendeur, l'article et la date déjà joints. Bien plus pratique à regrouper.

Pour découvrir les champs disponibles : `odoo_fields("sale.report")`.

## Limites connues

- Les **indicateurs chiffrés** et les **jauges** ne sont pas générés (voir plus haut).
- Les **filtres globaux** d'un tableau de bord ne sont pas générés : ils exigent un
  `fieldMatching` par graphique, à maintenir manuellement.
- La **version du format** est fixée à celle observée sur Odoo 19 (`18.5.10`). Odoo
  migre les anciens formats à l'ouverture, mais un format plus récent que le serveur
  poserait problème.
- Un tableau de bord généré est **remplaçable** (`dashboard_id`) mais pas fusionnable :
  la génération écrase le contenu.
