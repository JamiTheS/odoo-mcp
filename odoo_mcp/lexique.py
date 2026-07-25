"""Traduction du jargon Odoo en français métier.

Un rapport qui parle de « res.partner » et de « unlink » est illisible pour un dirigeant.
Ce module donne, pour chaque objet Odoo, son nom courant et l'endroit où on le trouve
dans l'interface — ce qui sert à la fois au rapport et au guide de présentation.
"""

from __future__ import annotations

# model technique -> (nom courant, module, chemin de menu)
MODELES: dict[str, tuple[str, str, str]] = {
    # Tiers
    "res.partner": ("Contacts (clients, fournisseurs)", "Contacts", "Contacts"),
    "res.partner.category": ("Étiquettes de contact", "Contacts",
                             "Contacts → Configuration → Étiquettes"),
    "res.users": ("Utilisateurs", "Paramètres", "Paramètres → Utilisateurs"),
    "res.company": ("Sociétés", "Paramètres", "Paramètres → Sociétés"),
    # Catalogue
    "product.template": ("Articles", "Ventes", "Ventes → Articles → Articles"),
    "product.product": ("Variantes d'article", "Ventes", "Ventes → Articles → Variantes"),
    "product.category": ("Catégories d'articles", "Ventes",
                         "Ventes → Configuration → Catégories"),
    "product.supplierinfo": ("Tarifs fournisseurs", "Achats",
                             "Onglet « Achat » de la fiche article"),
    "product.pricelist": ("Listes de prix", "Ventes", "Ventes → Configuration → Listes de prix"),
    # Ventes
    "sale.order": ("Devis et commandes clients", "Ventes", "Ventes → Commandes → Devis"),
    "sale.order.line": ("Lignes de commande client", "Ventes",
                        "Détail d'une commande client"),
    "crm.lead": ("Pistes et opportunités", "CRM", "CRM → Ventes → Mon pipeline"),
    # Achats
    "purchase.order": ("Commandes fournisseurs", "Achats",
                       "Achats → Commandes → Commandes fournisseurs"),
    "purchase.order.line": ("Lignes de commande fournisseur", "Achats",
                            "Détail d'une commande fournisseur"),
    # Stock
    "stock.picking": ("Livraisons et réceptions", "Inventaire", "Inventaire → Aperçu"),
    "stock.move": ("Mouvements de stock", "Inventaire",
                   "Inventaire → Rapport → Mouvements de stock"),
    "stock.quant": ("Stock disponible", "Inventaire",
                    "Inventaire → Rapport → Stock disponible"),
    "stock.location": ("Emplacements", "Inventaire",
                       "Inventaire → Configuration → Emplacements"),
    "stock.warehouse": ("Entrepôts", "Inventaire", "Inventaire → Configuration → Entrepôts"),
    "stock.route": ("Règles de réapprovisionnement", "Inventaire",
                    "Inventaire → Configuration → Routes"),
    # Comptabilité
    "account.move": ("Factures et pièces comptables", "Comptabilité",
                     "Comptabilité → Clients → Factures"),
    "account.move.line": ("Lignes comptables", "Comptabilité", "Détail d'une facture"),
    "account.payment": ("Paiements", "Comptabilité", "Comptabilité → Clients → Paiements"),
    "account.analytic.account": ("Sections analytiques (affaires, chantiers)",
                                 "Comptabilité",
                                 "Comptabilité → Configuration → Comptes analytiques"),
    "account.analytic.plan": ("Axes analytiques", "Comptabilité",
                              "Comptabilité → Configuration → Plans analytiques"),
    "account.analytic.line": ("Écritures analytiques (heures, coûts)", "Comptabilité",
                              "Comptabilité → Rapport → Analytique"),
    "account.tax": ("Taxes", "Comptabilité", "Comptabilité → Configuration → Taxes"),
    # Projets
    "project.project": ("Projets (chantiers, sites)", "Projet", "Projet → Projets"),
    "project.task": ("Tâches et jalons", "Projet", "À l'intérieur d'un projet"),
    "project.milestone": ("Jalons", "Projet", "Onglet « Jalons » du projet"),
    # RH
    "hr.employee": ("Salariés", "Employés", "Employés → Employés"),
    "hr.department": ("Services", "Employés", "Employés → Configuration → Services"),
    "hr.skill": ("Compétences et habilitations", "Employés",
                 "Employés → Configuration → Compétences"),
    "hr.employee.skill": ("Habilitations des salariés", "Employés",
                          "Onglet « CV / Compétences » de la fiche salarié"),
    "planning.slot": ("Planning des équipes", "Planning", "Planning → Planning"),
    # Divers
    "ir.attachment": ("Pièces jointes", "—", "Trombone en haut des fiches"),
    "mail.message": ("Messages et historique", "—", "Fil de discussion des fiches"),
}

# Verbes techniques -> langage courant
OPERATIONS = {
    "create": "Création",
    "write": "Modification",
    "unlink": "Suppression",
    "load": "Import de données",
    "action_confirm": "Validation",
    "button_confirm": "Validation",
    "action_post": "Comptabilisation",
    "button_validate": "Validation",
    "action_cancel": "Annulation",
    "toggle_active": "Archivage",
}


def nom_metier(model: str) -> str:
    """« res.partner » -> « Contacts (clients, fournisseurs) »."""
    entree = MODELES.get(model)
    if entree:
        return entree[0]
    # Repli lisible pour un modèle non répertorié (souvent un modèle sur mesure).
    if model.startswith("x_"):
        return model[2:].replace("_", " ").capitalize() + " (objet sur mesure)"
    return model.replace(".", " ").replace("_", " ").capitalize()


def module(model: str) -> str:
    entree = MODELES.get(model)
    return entree[1] if entree else "—"


def chemin(model: str) -> str:
    """Où trouver cet objet dans l'interface Odoo."""
    entree = MODELES.get(model)
    return entree[2] if entree else "—"


def operation(methode: str) -> str:
    return OPERATIONS.get(methode, methode.replace("_", " ").capitalize())
