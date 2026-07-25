"""Journal d'audit : tout ce qui est écrit dans Odoo, et pourquoi.

Le serveur MCP est le point de passage obligé de toute écriture : il peut donc
enregistrer chaque opération sans que personne ait à y penser. Le journal capture
l'état AVANT modification — c'est ce qui permet de dire « le téléphone est passé de X
à Y », et pas seulement « le contact 42 a été modifié ».

Écrit en JSONL (une ligne par opération) pour survivre à la fin de la session et rester
lisible/diffable. Le rapport HTML/Markdown est généré à la demande.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path

from odoo_mcp import lexique

IRREVERSIBLES = {"unlink"}


class Journal:
    """Accumule les opérations d'une session de travail et les persiste."""

    def __init__(self, path: Path, base: str, utilisateur: str = "",
                 titre: str = "", objectif: str = ""):
        self.path = path
        self.base = base
        self.utilisateur = utilisateur
        self.titre = titre or "Session de travail"
        self.objectif = objectif
        self.chapitre = ""
        self.motif = ""
        self.debut = datetime.now()
        self.entrees: list[dict] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ecrire({
            "type": "session",
            "ts": self.debut.isoformat(timespec="seconds"),
            "titre": self.titre,
            "objectif": objectif,
            "base": base,
            "utilisateur": utilisateur,
        })

    # ---------------------------------------------------------------- écriture
    def _ecrire(self, entree: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entree, ensure_ascii=False, default=str) + "\n")

    def ouvrir_chapitre(self, nom: str, pourquoi: str = "") -> None:
        """Regroupe les opérations suivantes sous une étape métier."""
        self.chapitre = nom
        entree = {"type": "chapitre", "ts": _now(), "nom": nom, "pourquoi": pourquoi}
        self.entrees.append(entree)
        self._ecrire(entree)

    def noter(self, texte: str, categorie: str = "note") -> None:
        """Consigne une décision, une observation, un point d'attention."""
        entree = {"type": categorie, "ts": _now(), "chapitre": self.chapitre,
                  "texte": texte}
        self.entrees.append(entree)
        self._ecrire(entree)

    def enregistrer(self, model: str, methode: str, nb: int,
                    ids: list | None = None, avant: list | None = None,
                    apres=None, motif: str = "", outil: str = "") -> None:
        entree = {
            "type": "operation",
            "ts": _now(),
            "chapitre": self.chapitre,
            "model": model,
            "objet": lexique.nom_metier(model),
            "methode": methode,
            "operation": lexique.operation(methode),
            "nb": nb,
            "ids": (ids or [])[:200],
            "avant": (avant or [])[:20],
            "apres": apres,
            "motif": motif or self.motif,
            "irreversible": methode in IRREVERSIBLES,
            "outil": outil,
        }
        self.entrees.append(entree)
        self._ecrire(entree)

    # ---------------------------------------------------------------- lecture
    @staticmethod
    def charger(path: Path) -> tuple[dict, list[dict]]:
        """Relit un journal existant : (métadonnées de session, entrées)."""
        if not path.exists():
            raise FileNotFoundError(path)
        session, entrees = {}, []
        for ligne in path.read_text(encoding="utf-8").splitlines():
            if not ligne.strip():
                continue
            e = json.loads(ligne)
            if e.get("type") == "session":
                session = e
            else:
                entrees.append(e)
        return session, entrees

    def synthese(self) -> dict:
        """Chiffres clés : volumes par opération et par modèle."""
        ops = [e for e in self.entrees if e["type"] == "operation"]
        par_operation: dict[str, int] = {}
        par_model: dict[str, dict[str, int]] = {}
        for e in ops:
            par_operation[e["operation"]] = par_operation.get(e["operation"], 0) + e["nb"]
            slot = par_model.setdefault(e["model"], {})
            slot[e["operation"]] = slot.get(e["operation"], 0) + e["nb"]
        return {
            "operations": len(ops),
            "enregistrements": sum(e["nb"] for e in ops),
            "par_operation": par_operation,
            "par_model": par_model,
            "irreversibles": sum(e["nb"] for e in ops if e["irreversible"]),
            "sans_motif": sum(1 for e in ops if not e["motif"]),
        }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _heure(ts: str) -> str:
    return ts[11:16] if len(ts) >= 16 else ts


def _valeur(v) -> str:
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int):
        return str(v[1])
    if v is False or v is None or v == "":
        return "(vide)"
    return str(v)


# --------------------------------------------------------------------- rapports
def rendre_markdown(session: dict, entrees: list[dict], synthese: dict) -> str:
    L = []
    L.append(f"# {session.get('titre', 'Session de travail')}")
    L.append("")
    if session.get("objectif"):
        L.append(f"**Objectif :** {session['objectif']}")
        L.append("")
    L.append(f"- **Base :** {session.get('base', '?')}")
    L.append(f"- **Utilisateur :** {session.get('utilisateur', '?')}")
    L.append(f"- **Début :** {session.get('ts', '?').replace('T', ' à ')}")
    L.append("")
    L.append("## Ce qui a été fait")
    L.append("")
    L.append(f"{synthese['enregistrements']} enregistrements touchés "
             f"en {synthese['operations']} opérations.")
    L.append("")
    L.append("| Opération | Enregistrements |")
    L.append("|---|---|")
    for op, n in sorted(synthese["par_operation"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {op} | {n} |")
    L.append("")
    if synthese["irreversibles"]:
        L.append(f"> {synthese['irreversibles']} suppressions définitives — "
                 "opérations irréversibles.")
        L.append("")

    if synthese.get("par_model"):
        L.append("### Par type d'information")
        L.append("")
        L.append("| Ce qui a été touché | Détail | Total |")
        L.append("|---|---|---|")
        for model, ops in sorted(synthese["par_model"].items(),
                                 key=lambda kv: -sum(kv[1].values())):
            detail = ", ".join(f"{op.lower()} : {n}" for op, n in sorted(ops.items()))
            L.append(f"| {lexique.nom_metier(model)} | {detail} | {sum(ops.values())} |")
        L.append("")

    L.append("## Déroulé")
    L.append("")
    for e in entrees:
        if e["type"] == "chapitre":
            L.append("")
            L.append(f"### {e['nom']}")
            if e.get("pourquoi"):
                L.append("")
                L.append(f"*{e['pourquoi']}*")
            L.append("")
        elif e["type"] == "operation":
            motif = f" — {e['motif']}" if e.get("motif") else ""
            marque = " **(irréversible)**" if e["irreversible"] else ""
            objet = e.get("objet") or lexique.nom_metier(e["model"])
            L.append(f"- `{_heure(e['ts'])}` **{e['operation']}** de {e['nb']} — "
                     f"{objet}{marque}{motif}")
            if e["methode"] == "unlink":
                for nom in _noms_supprimes(e):
                    L.append(f"  - supprimé : {nom}")
            else:
                for a in (e.get("avant") or [])[:3]:
                    champs = {k: v for k, v in a.items()
                              if k not in ("id", "display_name")}
                    if champs and isinstance(e.get("apres"), dict):
                        detail = ", ".join(
                            f"{k} : {_valeur(v)} → {_valeur(e['apres'].get(k, v))}"
                            for k, v in champs.items())
                        L.append(f"  - {a.get('display_name', a.get('id'))} — {detail}")
        else:
            L.append(f"- `{_heure(e['ts'])}` *{e.get('texte', '')}*")
    L.append("")
    return "\n".join(L)


def _noms_supprimes(entree: dict, maximum: int = 10) -> list[str]:
    """Ce qui a disparu — l'information la plus importante d'une suppression."""
    avant = entree.get("avant") or []
    noms = [str(a.get("display_name") or f"id {a.get('id')}") for a in avant[:maximum]]
    reste = entree["nb"] - len(noms)
    if reste > 0:
        noms.append(f"… et {reste} autre(s)")
    if not noms and entree.get("ids"):
        noms = [f"identifiants {entree['ids'][:maximum]}"]
    return noms


def rendre_html(session: dict, entrees: list[dict], synthese: dict) -> str:
    """Rapport autonome, présentable tel quel à un client."""
    NAVY, AMBER, GREY = "#0f2b46", "#f2a900", "#5b6b7a"
    parts = [f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(session.get('titre', 'Rapport'))}</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;font:15px/1.6 Calibri,Segoe UI,system-ui,sans-serif;color:#22303c;background:#fff}}
 .wrap{{max-width:1000px;margin:0 auto;padding:0 24px 64px}}
 header{{background:{NAVY};color:#fff;padding:40px 0 34px;margin-bottom:32px}}
 header .wrap{{padding-bottom:0}}
 h1{{font-family:Cambria,Georgia,serif;font-size:32px;margin:0 0 6px}}
 .kicker{{color:{AMBER};font-size:12px;font-weight:700;letter-spacing:2px;margin-bottom:10px}}
 .meta{{color:#c7d3de;font-size:13px;margin-top:14px}}
 .meta b{{color:#fff;font-weight:600}}
 h2{{font-family:Cambria,Georgia,serif;font-size:22px;color:{NAVY};margin:36px 0 14px}}
 h3{{font-size:16px;color:{NAVY};margin:26px 0 4px}}
 .cards{{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0 8px}}
 .card{{flex:1 1 150px;background:#f4f6f8;border-radius:8px;padding:16px 18px}}
 .card .n{{font-size:26px;font-weight:700;color:{NAVY}}}
 .card .l{{font-size:12px;color:{GREY};margin-top:2px}}
 table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}}
 th{{background:{NAVY};color:#fff;text-align:left;padding:8px 10px;font-weight:600}}
 td{{padding:7px 10px;border-bottom:1px solid #e6ebef}}
 tbody tr:nth-child(even){{background:#fafbfc}}
 .why{{color:{GREY};font-style:italic;margin:2px 0 12px}}
 .ou{{color:{GREY};font-size:12px;font-weight:400;margin-top:2px}}
 .op{{border-left:3px solid #dde3e8;padding:8px 0 8px 14px;margin:8px 0}}
 .op.irr{{border-left-color:#b3402f}}
 .op .h{{font-size:14px}}
 .op .t{{color:{GREY};font-size:12px;margin-right:8px;font-variant-numeric:tabular-nums}}
 .tag{{display:inline-block;background:{NAVY};color:#fff;border-radius:4px;
       padding:1px 7px;font-size:11px;margin-right:6px}}
 .tag.irr{{background:#b3402f}}
 .motif{{color:{GREY};font-size:13px;margin-top:2px}}
 .diff{{font-size:13px;margin-top:5px;color:#3c4a57}}
 .diff .k{{color:{GREY}}}
 .old{{background:#fbeeec;padding:1px 5px;border-radius:3px}}
 .new{{background:#eaf3ee;padding:1px 5px;border-radius:3px;font-weight:600}}
 .note{{background:#fdf3dc;border-radius:6px;padding:10px 14px;margin:10px 0;font-size:14px}}
 footer{{color:{GREY};font-size:12px;margin-top:40px;border-top:1px solid #e6ebef;padding-top:14px}}
 @media print{{header{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}}}
</style></head><body>
<header><div class="wrap">
 <div class="kicker">RAPPORT D'INTERVENTION ODOO</div>
 <h1>{escape(session.get('titre', 'Session de travail'))}</h1>"""]

    if session.get("objectif"):
        parts.append(f'<div style="color:#c7d3de;font-size:15px;max-width:70ch">'
                     f'{escape(session["objectif"])}</div>')
    parts.append(
        f'<div class="meta">Base <b>{escape(str(session.get("base", "?")))}</b> &nbsp;·&nbsp; '
        f'Utilisateur <b>{escape(str(session.get("utilisateur", "?")))}</b> &nbsp;·&nbsp; '
        f'Le <b>{escape(str(session.get("ts", "?")).replace("T", " à "))}</b></div>'
        '</div></header><div class="wrap">')

    parts.append("<h2>Ce qui a été fait</h2><div class='cards'>")
    parts.append(f'<div class="card"><div class="n">{synthese["enregistrements"]}</div>'
                 '<div class="l">enregistrements touchés</div></div>')
    parts.append(f'<div class="card"><div class="n">{synthese["operations"]}</div>'
                 '<div class="l">opérations</div></div>')
    for op, n in sorted(synthese["par_operation"].items(), key=lambda kv: -kv[1])[:3]:
        parts.append(f'<div class="card"><div class="n">{n}</div>'
                     f'<div class="l">{escape(op).lower()}</div></div>')
    parts.append("</div>")

    if synthese["par_model"]:
        parts.append("<table><thead><tr><th>Ce qui a été touché</th><th>Détail</th>"
                     "<th style='text-align:right'>Total</th></tr></thead><tbody>")
        for model, ops in sorted(synthese["par_model"].items(),
                                 key=lambda kv: -sum(kv[1].values())):
            detail = ", ".join(f"{op.lower()} : {n}" for op, n in sorted(ops.items()))
            parts.append(
                f"<tr><td><b>{escape(lexique.nom_metier(model))}</b>"
                f"<div class='ou'>{escape(lexique.chemin(model))}</div></td>"
                f"<td>{escape(detail)}</td>"
                f"<td style='text-align:right'><b>{sum(ops.values())}</b></td></tr>")
        parts.append("</tbody></table>")

    if synthese["irreversibles"]:
        parts.append(f'<div class="note"><b>{synthese["irreversibles"]} suppressions '
                     "définitives</b> ont été effectuées. Odoo n'a pas de corbeille : "
                     "ces enregistrements ne sont pas récupérables.</div>")

    parts.append("<h2>Déroulé détaillé</h2>")
    ouvert = False
    for e in entrees:
        if e["type"] == "chapitre":
            parts.append(f"<h3>{escape(e['nom'])}</h3>")
            if e.get("pourquoi"):
                parts.append(f"<div class='why'>{escape(e['pourquoi'])}</div>")
            ouvert = True
        elif e["type"] == "operation":
            cls = " irr" if e["irreversible"] else ""
            tag = '<span class="tag irr">IRRÉVERSIBLE</span>' if e["irreversible"] else ""
            objet = e.get("objet") or lexique.nom_metier(e["model"])
            parts.append(f'<div class="op{cls}"><div class="h"><span class="t">'
                         f'{_heure(e["ts"])}</span>{tag}<b>{escape(e["operation"])}</b> '
                         f'de {e["nb"]} — {escape(objet)}</div>')
            if e.get("motif"):
                parts.append(f'<div class="motif">{escape(e["motif"])}</div>')
            if e["methode"] == "unlink":
                for nom in _noms_supprimes(e):
                    parts.append(f'<div class="diff"><span class="old">'
                                 f'{escape(nom)}</span></div>')
            else:
                apres = e.get("apres") if isinstance(e.get("apres"), dict) else {}
                for a in (e.get("avant") or [])[:5]:
                    champs = {k: v for k, v in a.items()
                              if k not in ("id", "display_name")}
                    if not champs:
                        continue
                    diffs = " &nbsp; ".join(
                        f'<span class="k">{escape(k)}</span> '
                        f'<span class="old">{escape(_valeur(v))}</span> → '
                        f'<span class="new">{escape(_valeur(apres.get(k, v)))}</span>'
                        for k, v in champs.items())
                    nom = escape(str(a.get("display_name") or a.get("id")))
                    parts.append(f'<div class="diff">{nom} — {diffs}</div>')
            parts.append("</div>")
        else:
            parts.append(f'<div class="note">{escape(e.get("texte", ""))}</div>')
    if not ouvert and not entrees:
        parts.append("<p style='color:#5b6b7a'>Aucune opération enregistrée.</p>")

    parts.append(f'<footer>Rapport généré le '
                 f'{datetime.now().strftime("%d/%m/%Y à %H:%M")} par odoo-mcp. '
                 "Chaque opération listée est passée par le connecteur MCP et a été "
                 "journalisée automatiquement.</footer>")
    parts.append("</div></body></html>")
    return "\n".join(parts)
