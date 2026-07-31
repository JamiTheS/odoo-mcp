"""Auto-régulation du volume de réponse.

Trois principes, dans cet ordre de priorité :

1. **Ne jamais jeter une réponse.** Refuser en bloc gaspille l'aller-retour et oblige
   l'assistant à recommencer : c'est le pire des deux mondes, on paie les tokens de
   l'échec sans obtenir la donnée. On rogne progressivement jusqu'à ce que ça tienne.

2. **Toujours dire ce qui a été rogné, et comment obtenir le reste.** Une réponse
   tronquée en silence conduit à des conclusions fausses — bien pire qu'une réponse
   volumineuse.

3. **Se resserrer à mesure que la conversation s'allonge.** Les premiers appels d'une
   session peuvent être généreux ; au bout de cent mille caractères déjà consommés,
   les mêmes largesses saturent le contexte. Le serveur compte ce qu'il a rendu et
   ajuste ses valeurs par défaut tout seul.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

# Les résultats volumineux sont déposés ici plutôt que rendus dans la conversation.
# L'assistant dispose d'un outil de lecture de fichiers : lui donner un chemin coûte
# quelques dizaines de tokens là où la donnée en coûterait des dizaines de milliers.
DOSSIER_RESULTATS = Path.home() / "odoo-mcp-resultats"

# Rotation des déports : au-delà, les plus vieux sont supprimés au prochain déport.
PURGE_AGE_JOURS = 7
PURGE_MAX_FICHIERS = 50

# Estimation tokens : le JSON dense (guillemets, accolades, accents) tourne autour de
# 3,5 caractères par token, plus bas que la prose française. Reste une approximation
# assumée — aucun tokeniseur n'est embarqué pour ne pas alourdir l'installation.
CARACTERES_PAR_TOKEN = 3.5

# Paliers de consommation cumulée (en caractères rendus depuis le début de la session).
# À 3,5 caractères par token, 200 000 caractères ~= 57 000 tokens.
PALIERS = [
    # (seuil, nom, taille max d'une reponse, champs par defaut, limite par defaut)
    (0,       "confort",  60_000, 12, 50),
    (200_000, "economie", 30_000,  8, 30),
    (500_000, "strict",   15_000,  6, 20),
]


class Budget:
    """Compte ce que le serveur a rendu et resserre les défauts en conséquence."""

    def __init__(self) -> None:
        self.rendu = 0          # caractères effectivement renvoyés
        self.economise = 0      # caractères évités par les troncatures
        self.troncatures = 0
        self.deportes = 0       # résultats complets écrits sur disque

    # -- palier courant
    @property
    def palier(self) -> tuple[int, str, int, int, int]:
        courant = PALIERS[0]
        for p in PALIERS:
            if self.rendu >= p[0]:
                courant = p
        return courant

    @property
    def nom_palier(self) -> str:
        return self.palier[1]

    @property
    def taille_max(self) -> int:
        return self.palier[2]

    @property
    def champs_par_defaut(self) -> int:
        return self.palier[3]

    @property
    def limite_par_defaut(self) -> int:
        return self.palier[4]

    def etat(self) -> dict:
        return {
            "palier": self.nom_palier,
            "caracteres_rendus": self.rendu,
            "tokens_estimes": int(self.rendu / CARACTERES_PAR_TOKEN),
            "caracteres_economises": self.economise,
            "tokens_economises": int(self.economise / CARACTERES_PAR_TOKEN),
            "troncatures": self.troncatures,
            "resultats_deportes": self.deportes,
            "reglages_courants": {
                "taille_max_reponse": self.taille_max,
                "champs_par_defaut": self.champs_par_defaut,
                "limite_par_defaut": self.limite_par_defaut,
            },
        }

    # -- sérialisation régulée
    def rendre(self, data) -> str:
        """Sérialise en restant sous le plafond, en rognant plutôt qu'en refusant."""
        compact = _dumps(data)
        plafond = self.taille_max
        if len(compact) <= plafond:
            self.rendu += len(compact)
            return compact

        entier = len(compact)
        # Avant de rogner : déposer l'intégralité sur disque. Le rognage devient alors
        # une commodité de lecture et non une perte — rien n'est inaccessible.
        fichier = _deporter(data)
        reduit = _reduire(data, plafond)
        if fichier and isinstance(reduit, dict):
            reduit["fichier_complet"] = str(fichier)
            reduit["lire_le_reste"] = ("Ouvre ce fichier avec ton outil de lecture pour "
                                       "obtenir l'intégralité sans repasser par Odoo.")
            self.deportes += 1
        sortie = _dumps(reduit)

        # Dernier recours : la structure ne se prête pas au rognage (texte massif).
        if len(sortie) > plafond:
            sortie = _dumps({
                "tronque": True,
                "taille_origine": entier,
                "raison": "Réponse trop volumineuse pour être rendue en entier.",
                "comment_obtenir_le_reste": "Restreins `fields`, baisse `limit`, "
                                            "ou affine le domaine.",
                "debut": compact[:plafond - 400],
            })
        sortie = sortie[:plafond]   # coupe dure : rien ne dépasse le plafond

        self.troncatures += 1
        self.economise += max(entier - len(sortie), 0)
        self.rendu += len(sortie)
        return sortie


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _deporter(data) -> Path | None:
    """Écrit l'intégralité du résultat sur disque et retourne son chemin.

    Idée reprise du stockage par référence : plutôt que de faire transiter des
    dizaines de milliers de tokens par la conversation, on dépose la donnée là où
    l'assistant peut la relire à la demande, et on ne rend qu'un chemin.
    """
    try:
        DOSSIER_RESULTATS.mkdir(parents=True, exist_ok=True)
        _purger()
        nom = f"resultat_{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.json"
        chemin = DOSSIER_RESULTATS / nom
        chemin.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str),
                          encoding="utf-8")
        return chemin
    except OSError:
        return None      # un disque plein ne doit pas faire échouer la requête


def _purger() -> None:
    """Supprime les déports trop vieux et plafonne le nombre de fichiers conservés."""
    try:
        fichiers = sorted(DOSSIER_RESULTATS.glob("resultat_*.json"),
                          key=lambda p: p.stat().st_mtime)
        limite = time.time() - PURGE_AGE_JOURS * 86400
        for p in fichiers:
            if p.stat().st_mtime < limite:
                p.unlink(missing_ok=True)
        fichiers = [p for p in fichiers if p.exists()]
        for p in fichiers[:-PURGE_MAX_FICHIERS]:
            p.unlink(missing_ok=True)
    except OSError:
        pass           # une purge en échec ne doit jamais faire échouer la requête


def _cle_liste(data: dict) -> str | None:
    """Repère la liste de résultats dans un dictionnaire de réponse."""
    candidates = [k for k, v in data.items() if isinstance(v, list) and len(v) > 1]
    if not candidates:
        return None
    # La plus volumineuse est celle qui coûte.
    return max(candidates, key=lambda k: len(_dumps(data[k])))


def _reduire(data, plafond: int):
    """Rogne la liste de résultats jusqu'à tenir, en annonçant ce qui manque."""
    if isinstance(data, list):
        gardees = _combien_tiennent(data, plafond - 300)
        if gardees >= len(data):
            return data
        return {
            "tronque": True,
            "affiches": gardees,
            "total_dans_la_reponse": len(data),
            "comment_obtenir_la_suite": f"Rappelle avec offset={gardees}, "
                                        "ou restreins `fields` / le domaine.",
            "resultats": data[:gardees],
        }

    if isinstance(data, dict):
        cle = _cle_liste(data)
        if cle is None:
            return data
        reste = {k: v for k, v in data.items() if k != cle}
        marge = plafond - len(_dumps(reste)) - 300
        lignes = data[cle]
        gardees = _combien_tiennent(lignes, max(marge, 500))
        if gardees >= len(lignes):
            return data
        return {
            **reste,
            "tronque": True,
            "affiches": gardees,
            "total_dans_la_reponse": len(lignes),
            "comment_obtenir_la_suite": f"Rappelle avec offset={gardees}, "
                                        "ou restreins `fields` / le domaine.",
            cle: lignes[:gardees],
        }
    return data


def _combien_tiennent(lignes: list, marge: int) -> int:
    """Nombre d'éléments qui tiennent dans la marge, par estimation puis ajustement."""
    if not lignes:
        return 0
    echantillon = _dumps(lignes[: min(10, len(lignes))])
    moyenne = max(len(echantillon) // min(10, len(lignes)), 1)
    estime = max(marge // moyenne, 1)
    n = min(estime, len(lignes))
    while n > 1 and len(_dumps(lignes[:n])) > marge:
        n = int(n * 0.8)
    return max(n, 1)
