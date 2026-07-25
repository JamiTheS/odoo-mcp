"""Guide de présentation : le parcours à suivre pour montrer le flux à un client.

Le journal sait quels objets ont été touchés. Ce module en déduit un déroulé de
démonstration dans l'ordre naturel du métier — devis, commande, livraison, facture —
avec le chemin de menu exact, les clics à faire, et une phrase d'accroche par étape.

L'idée : avoir sous les yeux, en réunion, une check-list qui évite d'oublier une étape
tout en gardant l'attention du client.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from odoo_mcp import lexique

# Le déroulé canonique d'une démonstration Odoo, dans l'ordre où on la raconte.
# Une étape n'apparaît dans le guide que si l'un de ses `models` a été touché.
ETAPES = [
    {
        "cle": "contacts",
        "titre": "Les clients et fournisseurs",
        "models": ["res.partner", "res.partner.category"],
        "module": "Contacts",
        "chemin": "Contacts",
        "actions": [
            "Ouvrir le module Contacts",
            "Montrer la fiche d'un client réel de la démonstration",
            "Faire défiler : coordonnées, contacts rattachés, historique commercial",
        ],
        "accroche": "Tout part du contact : une seule fiche, et toute l'activité "
                    "commerciale, logistique et comptable s'y rattache automatiquement.",
    },
    {
        "cle": "articles",
        "titre": "Le catalogue d'articles",
        "models": ["product.template", "product.product", "product.category",
                   "product.supplierinfo"],
        "module": "Ventes",
        "chemin": "Ventes → Articles → Articles",
        "actions": [
            "Ouvrir un article et montrer l'onglet « Ventes » (prix de vente)",
            "Montrer l'onglet « Achat » : le fournisseur et le prix négocié y sont portés",
            "Signaler la référence interne, qui sert de langage commun à toute l'équipe",
        ],
        "accroche": "Le catalogue est le socle : sans lui, chacun ressaisit les prix et "
                    "les références à sa façon. Ici, le prix négocié suit l'article.",
    },
    {
        "cle": "projet",
        "titre": "Les projets et chantiers",
        "models": ["project.project", "project.task", "project.milestone"],
        "module": "Projet",
        "chemin": "Projet → Projets",
        "actions": [
            "Ouvrir le projet créé pour la démonstration",
            "Montrer les tâches / jalons et leur enchaînement",
            "Ouvrir une tâche : responsable, échéance, temps prévu",
        ],
        "accroche": "Le projet regroupe tout ce qui concerne un même chantier, quelle que "
                    "soit sa durée — on retrouve l'historique complet en une recherche.",
    },
    {
        "cle": "devis",
        "titre": "Du devis à la commande client",
        "models": ["sale.order", "sale.order.line"],
        "module": "Ventes",
        "chemin": "Ventes → Commandes → Devis",
        "actions": [
            "Ouvrir un devis de la démonstration",
            "Montrer les lignes : articles, quantités, prix",
            "Expliquer le bouton « Confirmer » : le devis devient une commande ferme",
            "Signaler que la confirmation déclenche automatiquement la suite "
            "(livraison à préparer, facture à établir)",
        ],
        "accroche": "C'est le point de bascule : un clic sur « Confirmer », et le reste "
                    "de la chaîne se met en route tout seul.",
    },
    {
        "cle": "achats",
        "titre": "Les achats fournisseurs",
        "models": ["purchase.order", "purchase.order.line"],
        "module": "Achats",
        "chemin": "Achats → Commandes → Commandes fournisseurs",
        "actions": [
            "Ouvrir une commande fournisseur",
            "Montrer que les prix viennent de la fiche article, sans ressaisie",
            "Expliquer « Confirmer la commande » : la réception attendue est créée",
        ],
        "accroche": "Plus de ressaisie des références fournisseur : on commande dans le "
                    "catalogue, aux prix négociés.",
    },
    {
        "cle": "reception",
        "titre": "La réception et le stock",
        "models": ["stock.picking", "stock.move", "stock.quant"],
        "module": "Inventaire",
        "chemin": "Inventaire → Aperçu",
        "actions": [
            "Montrer les tuiles : réceptions à traiter, livraisons à préparer",
            "Ouvrir une réception et cliquer sur « Valider »",
            "Retourner sur l'article : la quantité en stock a bougé",
        ],
        "accroche": "Le stock n'est plus un fichier à part : il se met à jour tout seul, "
                    "au rythme des réceptions et des livraisons.",
    },
    {
        "cle": "livraison",
        "titre": "La livraison au client",
        "models": ["stock.picking"],
        "module": "Inventaire",
        "chemin": "Inventaire → Aperçu → Livraisons",
        "actions": [
            "Ouvrir la livraison issue de la commande client",
            "Valider la livraison",
            "Revenir sur la commande : les quantités livrées y apparaissent",
        ],
        "accroche": "Ce qui est livré remonte automatiquement sur la commande : c'est ce "
                    "qui permet de facturer juste, sans repointer à la main.",
    },
    {
        "cle": "temps",
        "titre": "Les heures passées",
        "models": ["account.analytic.line", "hr.employee", "planning.slot"],
        "module": "Feuilles de temps",
        "chemin": "Feuilles de temps → Mes feuilles de temps",
        "actions": [
            "Montrer les heures saisies sur le chantier de la démonstration",
            "Expliquer que chaque heure porte un coût, imputé au chantier",
            "Ouvrir le projet : le total des heures y est consolidé",
        ],
        "accroche": "Chaque heure saisie sert deux fois : elle alimente le coût du "
                    "chantier, et elle prépare les éléments variables de paie.",
    },
    {
        "cle": "facture",
        "titre": "La facturation client",
        "models": ["account.move", "account.move.line"],
        "module": "Comptabilité",
        "chemin": "Comptabilité → Clients → Factures",
        "actions": [
            "Depuis la commande client, cliquer sur « Créer une facture »",
            "Montrer que les lignes sont reprises automatiquement",
            "Cliquer sur « Confirmer » : la facture est comptabilisée",
            "Montrer le bouton « Enregistrer un paiement »",
        ],
        "accroche": "Zéro ressaisie entre la commande et la facture : c'est là que le "
                    "temps administratif disparaît, et que les erreurs avec lui.",
    },
    {
        "cle": "analytique",
        "titre": "La rentabilité par affaire",
        "models": ["account.analytic.account", "account.analytic.plan",
                   "account.analytic.line"],
        "module": "Comptabilité",
        "chemin": "Comptabilité → Rapport → Analytique",
        "actions": [
            "Ouvrir la section analytique du chantier",
            "Montrer les recettes d'un côté, les coûts de l'autre",
            "Faire apparaître la marge réelle",
        ],
        "accroche": "C'est le juge de paix : ce que l'affaire a rapporté, ce qu'elle a "
                    "coûté, et ce qu'il en reste. Disponible en continu, pas en fin d'année.",
    },
    {
        "cle": "salaries",
        "titre": "Les salariés et leurs habilitations",
        "models": ["hr.employee", "hr.employee.skill", "hr.skill", "hr.department"],
        "module": "Employés",
        "chemin": "Employés → Employés",
        "actions": [
            "Ouvrir une fiche salarié",
            "Aller dans l'onglet des compétences / habilitations",
            "Montrer les dates de validité et celles qui arrivent à échéance",
        ],
        "accroche": "Les habilitations portent leurs dates de validité : on sait à "
                    "l'avance qui doit être recyclé, avant que ce soit bloquant.",
    },
]

# Modèles trop techniques pour mériter une étape de démonstration à eux seuls.
DISCRETS = {"ir.attachment", "ir.model.data", "mail.message", "res.users", "res.company"}


def construire(models_touches: set[str], session: dict,
               exemples: dict[str, list[str]] | None = None) -> list[dict]:
    """Sélectionne et ordonne les étapes pertinentes au vu de ce qui a été fait."""
    exemples = exemples or {}
    guide = []
    for etape in ETAPES:
        concernes = [m for m in etape["models"] if m in models_touches]
        if not concernes:
            continue
        noms = []
        for m in concernes:
            noms.extend(exemples.get(m, []))
        guide.append({**etape, "objets": concernes, "exemples": noms[:5]})

    couverts = {m for e in guide for m in e["models"]}
    restants = sorted(models_touches - couverts - DISCRETS)
    if restants:
        guide.append({
            "cle": "autres",
            "titre": "Les autres éléments mis en place",
            "module": "—",
            "chemin": "—",
            "objets": restants,
            "exemples": [],
            "actions": [f"Montrer « {lexique.nom_metier(m)} » — {lexique.chemin(m)}"
                        for m in restants],
            "accroche": "Ces éléments complètent le socle et n'ont pas besoin d'être "
                        "détaillés, sauf si le client pose la question.",
        })
    return guide


# ------------------------------------------------------------------- rendus
def rendre_markdown(session: dict, guide: list[dict], synthese: dict) -> str:
    L = [f"# Guide de présentation — {session.get('titre', 'démonstration')}", ""]
    if session.get("objectif"):
        L += [f"**Ce qu'on veut montrer :** {session['objectif']}", ""]
    L += [f"**Base de démonstration :** {session.get('base', '?')}", ""]
    L += ["## Avant de commencer", "",
          "- Se connecter à Odoo et garder l'onglet ouvert",
          "- Vérifier que les données de démonstration sont bien visibles",
          "- Prévoir 5 minutes par étape ci-dessous", ""]
    L += [f"## Le parcours ({len(guide)} étapes)", ""]
    for i, e in enumerate(guide, 1):
        L += [f"### Étape {i} — {e['titre']}", ""]
        if e["chemin"] != "—":
            L += [f"**Où aller :** {e['chemin']}", ""]
        for a in e["actions"]:
            L.append(f"- [ ] {a}")
        if e["exemples"]:
            L += ["", f"*À montrer :* {', '.join(e['exemples'])}"]
        L += ["", f"> **À dire :** {e['accroche']}", ""]
    L += ["## Pour conclure", "",
          f"En résumé : {synthese['enregistrements']} enregistrements ont été mis en place "
          f"pour cette démonstration.",
          "", "- Rappeler le bénéfice principal : une seule saisie, reprise à chaque étape",
          "- Demander au client quelle étape il souhaite approfondir",
          "- Noter les demandes d'ajustement pour la prochaine séance", ""]
    return "\n".join(L)


def rendre_html(session: dict, guide: list[dict], synthese: dict) -> str:
    NAVY, AMBER, GREY = "#0f2b46", "#f2a900", "#5b6b7a"
    P = [f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Guide de présentation — {escape(session.get('titre', ''))}</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;font:15px/1.65 Calibri,Segoe UI,system-ui,sans-serif;color:#22303c;background:#fff}}
 .wrap{{max-width:940px;margin:0 auto;padding:0 24px 64px}}
 header{{background:{NAVY};color:#fff;padding:38px 0 32px;margin-bottom:28px}}
 header .wrap{{padding-bottom:0}}
 h1{{font-family:Cambria,Georgia,serif;font-size:30px;margin:0 0 8px}}
 .kicker{{color:{AMBER};font-size:12px;font-weight:700;letter-spacing:2px;margin-bottom:10px}}
 .sub{{color:#c7d3de;font-size:15px;max-width:70ch}}
 h2{{font-family:Cambria,Georgia,serif;font-size:21px;color:{NAVY};margin:34px 0 12px}}
 .prep{{background:#f4f6f8;border-radius:8px;padding:16px 20px;margin:16px 0}}
 .prep li{{margin:4px 0}}
 .etape{{border:1px solid #e1e7ec;border-radius:10px;padding:20px 22px;margin:18px 0}}
 .num{{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;
       border-radius:50%;background:{AMBER};color:{NAVY};font-weight:700;font-size:14px;
       margin-right:10px;flex:none}}
 .etape h3{{display:flex;align-items:center;font-size:19px;color:{NAVY};margin:0 0 12px}}
 .ou{{background:#eef2f6;border-radius:6px;padding:8px 12px;font-size:14px;margin-bottom:12px}}
 .ou b{{color:{NAVY}}}
 ul.actions{{list-style:none;padding:0;margin:0 0 12px}}
 ul.actions li{{padding:5px 0 5px 30px;position:relative;font-size:14.5px}}
 ul.actions li:before{{content:"";position:absolute;left:2px;top:9px;width:15px;height:15px;
        border:2px solid #b9c4cd;border-radius:3px}}
 .exemples{{font-size:13.5px;color:{GREY};margin-bottom:12px}}
 .exemples b{{color:#22303c}}
 .dire{{background:#fdf3dc;border-radius:6px;padding:11px 15px;font-size:14.5px}}
 .dire b{{color:#8a6100;display:block;font-size:11px;letter-spacing:1.5px;margin-bottom:3px}}
 footer{{color:{GREY};font-size:12px;margin-top:36px;border-top:1px solid #e6ebef;padding-top:14px}}
 @media print{{header{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
               .etape{{break-inside:avoid}}}}
</style></head><body>
<header><div class="wrap">
 <div class="kicker">GUIDE DE PRÉSENTATION</div>
 <h1>{escape(session.get('titre', 'Démonstration'))}</h1>"""]
    if session.get("objectif"):
        P.append(f'<div class="sub">{escape(session["objectif"])}</div>')
    P.append(f'<div style="color:#8fa3b5;font-size:13px;margin-top:12px">'
             f'Base de démonstration : {escape(str(session.get("base", "?")))} '
             f'&nbsp;·&nbsp; {len(guide)} étapes</div></div></header><div class="wrap">')

    P.append('<h2>Avant de commencer</h2><div class="prep"><ul>'
             '<li>Se connecter à Odoo et garder l\'onglet ouvert, prêt à projeter</li>'
             '<li>Vérifier que les données de démonstration sont visibles</li>'
             '<li>Compter environ 5 minutes par étape</li>'
             '<li>Cocher les cases au fur et à mesure pour ne rien oublier</li>'
             '</ul></div>')

    P.append("<h2>Le parcours</h2>")
    for i, e in enumerate(guide, 1):
        P.append(f'<div class="etape"><h3><span class="num">{i}</span>'
                 f'{escape(e["titre"])}</h3>')
        if e["chemin"] != "—":
            P.append(f'<div class="ou"><b>Où aller :</b> {escape(e["chemin"])}</div>')
        P.append('<ul class="actions">')
        for a in e["actions"]:
            P.append(f"<li>{escape(a)}</li>")
        P.append("</ul>")
        if e["exemples"]:
            P.append(f'<div class="exemples">À montrer : '
                     f'<b>{escape(", ".join(e["exemples"]))}</b></div>')
        P.append(f'<div class="dire"><b>À DIRE</b>{escape(e["accroche"])}</div></div>')

    P.append('<h2>Pour conclure</h2><div class="prep"><ul>'
             f'<li>{synthese["enregistrements"]} enregistrements ont été mis en place '
             'pour cette démonstration</li>'
             '<li>Rappeler le bénéfice principal : une seule saisie, reprise '
             'automatiquement à chaque étape suivante</li>'
             '<li>Demander quelle étape le client souhaite approfondir</li>'
             '<li>Noter les demandes d\'ajustement pour la prochaine séance</li>'
             '</ul></div>')
    P.append(f'<footer>Guide généré le {datetime.now().strftime("%d/%m/%Y à %H:%M")} '
             "à partir des opérations réellement effectuées dans la base.</footer>")
    P.append("</div></body></html>")
    return "\n".join(P)
