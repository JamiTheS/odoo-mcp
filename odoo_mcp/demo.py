"""Mode démonstration : questionnaire de qualification et filet de sécurité e-mail.

Deux choses seulement vivent ici, et c'est volontaire :

1. **Le questionnaire de qualification.** C'est un document, pas du code. Il sert à
   cadrer l'échange avec l'avant-vente ; c'est ensuite l'assistant qui compose la
   maquette à partir des réponses. Aucune logique de génération côté serveur : un
   modèle de langage produira toujours des noms d'articles et un vocabulaire métier
   plus crédibles qu'un catalogue figé.

2. **Le filet e-mail.** Les bases de démonstration ne sont pas neutralisées : Odoo y
   envoie de vrais courriels à la confirmation d'une commande ou d'une facture. Une
   adresse réelle dans un jeu de données fictif, et un vrai client reçoit une fausse
   facture. Cela ne peut pas dépendre de la vigilance de l'assistant — le serveur
   réécrit systématiquement toute adresse vers un domaine réservé.
"""

from __future__ import annotations

import re

# example.com / .net / .org sont réservés par la RFC 2606 : ces domaines ne peuvent
# appartenir à personne, donc aucun courriel n'y parviendra jamais.
DOMAINE_SUR = "example.com"
DOMAINES_SURS = {"example.com", "example.net", "example.org", "example.edu",
                 "test", "invalid", "localhost"}

# Champs qui contiennent une adresse selon les modèles Odoo.
CHAMPS_EMAIL = {
    "email", "email_from", "email_cc", "email_bcc", "email_normalized",
    "partner_email", "work_email", "private_email", "email_formatted",
    "reply_to", "invoice_user_email", "catchall_email",
}

_ADRESSE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def domaine_sur(adresse: str, domaine: str = DOMAINE_SUR) -> bool:
    """L'adresse pointe-t-elle vers un domaine qui ne peut recevoir aucun courriel ?"""
    if not adresse or "@" not in adresse:
        return True
    hote = adresse.rsplit("@", 1)[-1].strip().lower().rstrip(">").strip()
    return hote == domaine.lower() or hote in DOMAINES_SURS


def neutraliser(adresse: str, domaine: str = DOMAINE_SUR) -> str:
    """Remplace le domaine d'une adresse en gardant la partie locale, donc la lisibilité.

    « jean.dupont@vraie-societe.fr » devient « jean.dupont@example.com » : l'adresse
    reste parlante à l'écran pendant la démonstration, mais n'aboutit nulle part.
    """
    if not adresse:
        return adresse

    def _remplacer(m: re.Match) -> str:
        locale = m.group(0).rsplit("@", 1)[0]
        return f"{locale}@{domaine}"

    if _ADRESSE.search(adresse):
        return _ADRESSE.sub(_remplacer, adresse)
    if "@" in adresse:                       # adresse malformée : on sécurise quand même
        return f"{adresse.rsplit('@', 1)[0]}@{domaine}"
    return adresse


def assainir_valeurs(valeurs: dict, domaine: str = DOMAINE_SUR) -> tuple[dict, int]:
    """Neutralise les adresses d'un dictionnaire de valeurs (create / write)."""
    if not isinstance(valeurs, dict):
        return valeurs, 0
    modifies = 0
    sortie = dict(valeurs)
    for champ, valeur in valeurs.items():
        if champ not in CHAMPS_EMAIL or not isinstance(valeur, str):
            continue
        if domaine_sur(valeur, domaine):
            continue
        sortie[champ] = neutraliser(valeur, domaine)
        modifies += 1
    return sortie, modifies


def assainir_lignes(champs: list[str], lignes: list[list],
                    domaine: str = DOMAINE_SUR) -> tuple[list[list], int]:
    """Même chose pour un import `load()`, où les données sont un tableau."""
    indices = [i for i, c in enumerate(champs) if c.split("/")[0] in CHAMPS_EMAIL]
    if not indices:
        return lignes, 0
    modifies = 0
    sortie = []
    for ligne in lignes:
        nouvelle = list(ligne)
        for i in indices:
            if i < len(nouvelle) and isinstance(nouvelle[i], str) \
                    and not domaine_sur(nouvelle[i], domaine):
                nouvelle[i] = neutraliser(nouvelle[i], domaine)
                modifies += 1
        sortie.append(nouvelle)
    return sortie, modifies


# --------------------------------------------------------------- questionnaire
QUESTIONNAIRE = """\
# Qualification avant maquette Odoo

## Pourquoi ces réponses comptent

La maquette est construite **à partir de ces seules réponses**. Une réponse vague donne
une démonstration générique, que le prospect ne reconnaîtra pas comme son métier — et une
démonstration qui ne ressemble pas au client fait plus de mal que pas de démonstration
du tout.

Comptez dix minutes. Répondez avec les mots du prospect, pas avec le vocabulaire Odoo :
c'est justement son vocabulaire que nous voulons retrouver à l'écran. Quand vous ne savez
pas, écrivez « je ne sais pas » plutôt que de deviner — nous prendrons une hypothèse
prudente et signalée, ce qui est préférable à une erreur affirmée.

---

## 1. L'entreprise

1.1 Nom de la société (ou un nom proche si vous préférez ne pas l'exposer)
1.2 Que fait-elle, en une phrase, comme elle le dirait elle-même
1.3 Combien de salariés, environ
1.4 Combien de clients actifs, environ — et sont-ce des entreprises ou des particuliers
1.5 Un ou plusieurs sites / dépôts / agences

## 2. Ce qu'elle vend

2.1 Vend-elle des **produits physiques**, des **prestations**, ou les deux
2.2 Citez cinq à dix exemples réels de ce qu'elle vend, avec les mots du métier
2.3 Ces ventes sont-elles **ponctuelles** ou **récurrentes** (abonnement, contrat annuel)
2.4 Y a-t-il des variantes (tailles, coloris, options) ou des configurations sur mesure

## 3. Les achats et le stock

3.1 Achète-t-elle pour revendre, pour transformer, ou n'achète-t-elle presque rien
3.2 **Tient-elle un stock ?** Si oui : dans un dépôt, sur les chantiers, chez le client
3.3 Achète-t-elle **à la commande** (pour une affaire précise) ou **sur seuil** (réassort)
3.4 Deux ou trois fournisseurs typiques, et ce qu'elle leur achète
3.5 Fabrique-t-elle quelque chose, ou assemble-t-elle

## 4. Comment le travail est piloté

4.1 L'unité de travail, c'est plutôt : une **commande** isolée, un **chantier / projet**
    qui dure, un **contrat** qui court, ou une **intervention** récurrente
4.2 Y a-t-il un **suivi du temps passé** par les équipes — et si oui, pourquoi faire
    (facturer le client, calculer un coût, préparer la paie)
4.3 Y a-t-il de la **sous-traitance**
4.4 Les équipes sont-elles **planifiées** (qui va où, quel jour)

## 5. La facturation

5.1 Facture-t-elle **à la commande**, **à la livraison**, **à l'avancement**, ou par
    **jalons / acomptes**
5.2 Y a-t-il des devis avant la commande, et combien de temps ils vivent
5.3 Un ajustement de fin de chantier / de contrat est-il courant (montant révisé)
5.4 Délais de paiement usuels, et est-ce un sujet douloureux

## 6. Ses clients à elle

Cette section est ce qui rend la maquette reconnaissable. Prenez le temps.

6.1 Trois à cinq **noms de clients types** (ou des noms crédibles du même genre)
6.2 Sont-ils tous du même secteur, ou est-ce varié
6.3 Un client représente-t-il une grosse part du chiffre d'affaires
6.4 Y a-t-il des **spécificités contractuelles** côté clients : marchés publics,
    contrats-cadres, commandes ouvertes, donneurs d'ordre imposant leurs règles
6.5 Le prospect a-t-il des exigences particulières venant de SES clients
    (documents à fournir, délais imposés, pénalités, certifications)

## 7. Le vocabulaire maison

7.1 Comment appelle-t-il ce que nous appellerions un « chantier », une « affaire »,
    un « dossier » — le mot exact qu'il emploie
7.2 Des abréviations ou termes métier qui reviennent dans la conversation
7.3 Des documents propres au métier qu'il a cités

## 8. Ce qu'il faut absolument montrer

8.1 Quel est **le problème** qu'il a mentionné pendant l'appel — celui qui lui fait perdre
    du temps ou de l'argent aujourd'hui
8.2 Quel outil utilise-t-il actuellement, et qu'est-ce qui coince avec
8.3 S'il ne devait retenir **qu'un seul écran** de la démonstration, lequel devrait
    l'emporter
8.4 Y a-t-il un sujet à **éviter** (budget, délais, un module qu'il a déjà refusé)

---

## Après le questionnaire

Un plan de maquette vous est proposé avant toute écriture : modules retenus, données qui
seront créées, volumes, flux de démonstration. Vous le validez ou l'ajustez, puis la
génération se lance.

Les adresses e-mail des données générées sont **systématiquement neutralisées** vers un
domaine réservé : aucun courriel ne peut partir vers une vraie entreprise, même si la base
n'est pas configurée en mode test.
"""

# Ce que l'assistant doit garder en tête en composant la maquette.
CONSIGNES_GENERATION = """\
Consignes pour composer la maquette à partir des réponses

Ce qui rend une démonstration crédible tient à peu de choses, et ce sont presque toujours
les mêmes qui manquent :

- **Le vocabulaire du prospect**, repris tel quel. C'est le premier signal de
  reconnaissance : il doit lire ses propres mots à l'écran, pas ceux d'Odoo.
- **L'étalement dans le temps.** Des documents tous datés d'aujourd'hui trahissent
  immédiatement la génération. Étalez sur six à douze mois, avec un entonnoir plausible :
  beaucoup de devis, moins de commandes confirmées, encore moins de factures payées.
- **La variété des états.** Tout ne doit pas être « terminé » : des devis en attente, des
  commandes en cours de livraison, des factures impayées. C'est ce qui donne l'impression
  d'une entreprise vivante.
- **Des montants cohérents** entre eux et avec la taille annoncée de l'entreprise.
- **Le point de douleur mis en scène.** Si le prospect a dit perdre du temps à retrouver
  ses affaires, la maquette doit contenir de quoi le démontrer.

Ordre de création à respecter : référentiels d'abord (contacts, articles, catégories),
puis les documents qui s'y rattachent, puis les mouvements et écritures.

Volumes indicatifs : quelques dizaines de contacts, une centaine d'articles, deux à trois
cents documents. Assez pour que les listes et les graphiques respirent, pas au point de
ralentir la base.

Ne composez rien avant d'avoir les réponses. Une hypothèse non signalée dans une
démonstration coûte plus cher qu'une question posée.
"""
