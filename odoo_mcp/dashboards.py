"""Génération de tableaux de bord Odoo (format o-spreadsheet).

Un tableau de bord Odoo est un classeur au format « o-spreadsheet », stocké en JSON
encodé en base64 dans le champ `spreadsheet_binary_data` de `spreadsheet.dashboard`.

Le point important : un graphique de type `odoo_bar` / `odoo_line` / `odoo_pie` est
**autonome**. Il porte son modèle, son regroupement, sa mesure et son filtre, et Odoo
recalcule les données à chaque ouverture. Pas de formule, pas de valeur figée, pas de
tableau croisé à maintenir — c'est ce qui rend la génération fiable.

À l'inverse, les indicateurs chiffrés (« scorecards ») des tableaux de bord livrés par
Odoo pointent vers des cellules qui contiennent des formules comptables en chaîne. On ne
les génère pas ici : trop fragile, et un graphique dit la même chose.
"""

from __future__ import annotations

import base64
import json
import uuid

# Types de graphiques exposés, avec leur libellé courant.
TYPES = {
    "bar": "histogramme (comparer des catégories)",
    "line": "courbe (suivre une évolution dans le temps)",
    "pie": "camembert (montrer une répartition)",
}

# Granularités acceptées derrière un champ date : "date_order:month"
GRANULARITES = {"day", "week", "month", "quarter", "year"}

LARGEUR_COLONNE = 96      # o-spreadsheet : largeur par défaut d'une colonne
HAUTEUR_LIGNE = 23


def _figure(titre: str, model: str, groupby: list[str], mesure: str, mode: str,
            domaine: list, col: int, row: int, largeur: int, hauteur: int,
            empile: bool = False) -> dict:
    """Construit une figure « graphique Odoo » — recalculée en direct par Odoo."""
    fid = str(uuid.uuid4())
    return {
        "id": fid,
        "width": largeur,
        "height": hauteur,
        "tag": "chart",
        "col": col,
        "row": row,
        "offset": {"x": 0, "y": 0},
        "data": {
            "title": {"text": titre, "bold": True, "fontSize": 16, "color": "#0F2B46"},
            "background": "#FFFFFF",
            "legendPosition": "right" if mode == "pie" else "top",
            "metaData": {
                "groupBy": groupby,
                "measure": mesure,
                "order": None,
                "resModel": model,
                "mode": mode,
                "cumulatedStart": False,
            },
            "searchParams": {
                "comparison": None,
                "context": {},
                "domain": domaine,
                "groupBy": groupby,
                "orderBy": [],
            },
            "type": f"odoo_{mode}",
            "dataSets": [],
            "humanize": True,
            "verticalAxisPosition": "left",
            "stacked": empile,
            "cumulatedStart": False,
            "fillArea": mode == "line",
            "chartId": fid,
            "fieldMatching": {},
        },
    }


def valider(client, graphiques: list[dict]) -> list[str]:
    """Vérifie modèles, champs de regroupement et mesures AVANT d'écrire.

    Un tableau de bord qui référence un champ inexistant s'ouvre vide, sans message
    d'erreur : mieux vaut refuser tout de suite et dire lequel pose problème.
    """
    problemes = []
    for i, g in enumerate(graphiques, 1):
        titre = g.get("titre") or f"graphique {i}"
        model = g.get("model")
        if not model:
            problemes.append(f"{titre} : champ 'model' manquant")
            continue
        mode = (g.get("type") or "bar").lower()
        if mode not in TYPES:
            problemes.append(f"{titre} : type '{mode}' inconnu "
                             f"(attendu {', '.join(TYPES)})")
        try:
            champs = client.fields_get(model)
        except Exception:
            problemes.append(f"{titre} : modèle '{model}' introuvable")
            continue

        groupby = g.get("groupby") or []
        if not groupby:
            problemes.append(f"{titre} : au moins un regroupement est nécessaire")
        for gb in groupby:
            nom, _, gran = str(gb).partition(":")
            if nom not in champs:
                problemes.append(f"{titre} : champ de regroupement '{nom}' inconnu "
                                 f"sur {model}")
            elif gran and gran not in GRANULARITES:
                problemes.append(f"{titre} : granularité '{gran}' inconnue "
                                 f"(attendu {', '.join(sorted(GRANULARITES))})")
            elif gran and champs[nom]["type"] not in ("date", "datetime"):
                problemes.append(f"{titre} : '{nom}' n'est pas une date, "
                                 "la granularité ne s'applique pas")

        mesure = g.get("mesure") or "__count"
        if mesure != "__count":
            if mesure not in champs:
                problemes.append(f"{titre} : mesure '{mesure}' inconnue sur {model}")
            elif champs[mesure]["type"] not in ("integer", "float", "monetary"):
                problemes.append(f"{titre} : la mesure '{mesure}' n'est pas numérique "
                                 f"(type {champs[mesure]['type']})")
    return problemes


def construire(titre: str, graphiques: list[dict], sous_titre: str = "") -> dict:
    """Assemble le classeur o-spreadsheet complet, en disposant les graphiques.

    Disposition : deux graphiques par ligne, sauf ceux marqués `pleine_largeur`.
    """
    figures = []
    col, row = 0, 3
    hauteur_bloc = 16

    for g in graphiques:
        mode = (g.get("type") or "bar").lower()
        pleine = bool(g.get("pleine_largeur"))
        largeur = 1160 if pleine else 570
        hauteur = 340
        if pleine and col != 0:
            col, row = 0, row + hauteur_bloc
        figures.append(_figure(
            titre=g.get("titre") or "",
            model=g["model"],
            groupby=[str(x) for x in (g.get("groupby") or [])],
            mesure=g.get("mesure") or "__count",
            mode=mode,
            domaine=g.get("domaine") or [],
            col=col, row=row, largeur=largeur, hauteur=hauteur,
            empile=bool(g.get("empile")),
        ))
        if pleine:
            col, row = 0, row + hauteur_bloc
        elif col == 0:
            col = 6
        else:
            col, row = 0, row + hauteur_bloc

    lignes_utiles = row + hauteur_bloc + 4
    cellules = {"A1": f'=_t("{titre}")'}
    styles_cellules = {"A1": 1}
    if sous_titre:
        cellules["A2"] = f'=_t("{sous_titre}")'
        styles_cellules["A2"] = 2

    return {
        "version": "18.5.10",
        "sheets": [{
            "id": "Sheet1",
            "name": "Tableau de bord",
            "colNumber": 12,
            "rowNumber": max(lignes_utiles, 40),
            "rows": {}, "cols": {},
            "cells": cellules,
            "styles": styles_cellules,
            "merges": [], "figures": figures,
            "conditionalFormats": [], "filterTables": [],
        }],
        "styles": {
            "1": {"bold": True, "fontSize": 20, "textColor": "#0F2B46"},
            "2": {"fontSize": 12, "textColor": "#5B6B7A", "italic": True},
        },
        "formats": {}, "borders": {},
        "revisionId": str(uuid.uuid4()),
        "uniqueFigureIds": True,
        "settings": {"locale": {
            "name": "French", "code": "fr_FR",
            "thousandsSeparator": " ", "decimalSeparator": ",",
            "dateFormat": "dd/mm/yyyy", "timeFormat": "hh:mm:ss",
            "formulaArgSeparator": ";",
        }},
        "pivots": {}, "pivotNextId": 1,
        "lists": {}, "listNextId": 1,
        "globalFilters": [], "customTableStyles": {},
        "chartOdooMenusReferences": {},
    }


def encoder(classeur: dict) -> str:
    return base64.b64encode(json.dumps(classeur).encode("utf-8")).decode("ascii")


def decoder(valeur) -> dict:
    brut = valeur.data if hasattr(valeur, "data") else valeur
    return json.loads(base64.b64decode(brut).decode("utf-8"))


def resumer(classeur: dict) -> dict:
    """Décrit en langage courant ce que contient un classeur existant."""
    feuilles, graphiques = [], []
    for sh in classeur.get("sheets", []):
        feuilles.append(sh.get("name") or sh.get("id"))
        for f in sh.get("figures") or []:
            d = f.get("data") or {}
            meta = d.get("metaData") or {}
            if meta:
                graphiques.append({
                    "titre": (d.get("title") or {}).get("text") or "(sans titre)",
                    "type": d.get("type"),
                    "source": meta.get("resModel"),
                    "regroupement": meta.get("groupBy"),
                    "mesure": meta.get("measure"),
                    "filtre": (d.get("searchParams") or {}).get("domain"),
                })
            else:
                graphiques.append({
                    "titre": (d.get("title") or {}).get("text") or "(sans titre)",
                    "type": d.get("type"),
                    "source": "cellule du classeur",
                    "cellule": d.get("keyValue"),
                })
    return {
        "feuilles": feuilles,
        "nb_graphiques": len(graphiques),
        "graphiques": graphiques,
        "tableaux_croises": list((classeur.get("pivots") or {}).keys()),
        "filtres_globaux": [f.get("label") for f in classeur.get("globalFilters") or []],
        "version_format": classeur.get("version"),
    }
