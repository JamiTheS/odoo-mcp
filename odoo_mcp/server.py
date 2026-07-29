"""Serveur MCP Odoo — connecteur XML-RPC pour tout client MCP (Claude, Gemini CLI, ...).

Deux modes de connexion :
  1. Variables d'environnement dans la config MCP du client (ODOO_URL, ODOO_USERNAME,
     ODOO_API_KEY, et optionnellement ODOO_DB / ODOO_ALLOW_WRITE) — connexion automatique.
  2. Aucune variable : l'assistant demande les identifiants dans la conversation et
     appelle l'outil `odoo_connect`. Ils ne vivent qu'en mémoire du processus.

L'écriture est bloquée par défaut ; elle s'active par l'outil `odoo_enable_write`,
à n'appeler qu'après accord explicite de l'utilisateur.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from datetime import datetime, timedelta

from odoo_mcp import dashboards, demo, files, presentation
from odoo_mcp.budget import Budget
from odoo_mcp.journal import Journal, rendre_html, rendre_markdown
from odoo_mcp.odoo_client import OdooClient, OdooError, ReadOnlyError, mask

DOSSIER_JOURNAUX = Path.home() / "odoo-mcp-journaux"

mcp = FastMCP("odoo")

_client: OdooClient | None = None


def _get_client() -> OdooClient:
    global _client
    if _client is None:
        if os.environ.get("ODOO_URL"):
            _client = OdooClient.from_env()
        else:
            raise OdooError(
                "Aucune connexion active. Demande à l'utilisateur l'URL de sa base, son "
                "login et sa clé API (générée SANS scope — une clé de scope 'MCP' est "
                "refusée en XML-RPC), puis appelle l'outil odoo_connect. "
                "Les identifiants ne sont jamais écrits sur disque."
            )
    return _client


# Champs volumineux ou sans intérêt en lecture : jamais renvoyés par défaut.
TYPES_LOURDS = {"binary", "html", "text", "json"}
PREFIXES_TECHNIQUES = ("message_", "activity_", "rating_", "website_message_",
                       "access_", "my_activity_", "alias_")

_budget = Budget()


def _j(data) -> str:
    """Sérialise compact, en rognant plutôt qu'en refusant si la réponse est trop grosse.

    Le plafond se resserre tout seul à mesure que la session consomme du contexte.
    """
    return _budget.rendre(data)


def _champs_par_defaut(client: OdooClient, model: str, maximum: int = 0) -> list[str]:
    """Choisit une poignée de champs utiles quand l'appelant n'en précise aucun.

    Sans cela, Odoo renvoie l'intégralité des champs — mesuré à 169 000 tokens pour
    dix contacts. Le tri privilégie les champs identifiants et courts, et le nombre
    retenu se réduit quand la session a déjà beaucoup consommé.
    """
    maximum = maximum or _budget.champs_par_defaut
    meta = client.fields_get(model, ["type", "string", "store"])
    prioritaires = [
        "display_name", "name", "default_code", "reference", "code", "partner_id",
        "date", "date_order", "date_deadline", "state", "stage_id", "user_id",
        "amount_untaxed", "amount_total", "list_price", "email", "phone", "city",
        "product_id", "project_id", "quantity", "product_uom_qty",
    ]
    retenus = [c for c in prioritaires if c in meta]
    if len(retenus) < maximum:
        for nom, info in meta.items():
            if len(retenus) >= maximum:
                break
            if nom in retenus or nom.startswith(PREFIXES_TECHNIQUES):
                continue
            if info.get("type") in TYPES_LOURDS or info.get("store") is False:
                continue
            if info.get("type") in ("char", "selection", "date", "datetime", "many2one",
                                    "integer", "float", "monetary", "boolean"):
                retenus.append(nom)
    return retenus[:maximum] or ["display_name"]


def _parse(value: str, default):
    if not value or not value.strip():
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise OdooError(f"JSON invalide : {value!r} ({exc.msg})")


def _require_write(c: OdooClient) -> None:
    if c.readonly:
        raise ReadOnlyError(
            "Mode lecture seule. Montre d'abord à l'utilisateur ce qui va être modifié, "
            "obtiens son accord, puis appelle odoo_enable_write."
        )


# ------------------------------------------------------------------- connexion
@mcp.tool()
def odoo_connect(url: str, username: str, api_key: str, db: str = "",
                 allow_write: bool = False) -> str:
    """Se connecter a une base Odoo — a appeler en premier. Identifiants gardes en
    memoire du serveur seulement, jamais ecrits sur disque. `db` se deduit du
    sous-domaine si omis. Laisser `allow_write` a False (voir odoo_enable_write).
    """
    global _client
    url = url.strip().rstrip("/")
    if not db:
        db = url.split("//")[-1].split("/")[0].split(".")[0]
    c = OdooClient(url=url, db=db, username=username.strip(), api_key=api_key.strip(),
                   readonly=not allow_write)
    version = c.version()
    uid = c.authenticate()
    user = c.read("res.users", [uid], ["name", "login"])[0]
    _client = c
    return _j({
        "status": "connecté",
        "url": url, "db": db,
        "server_version": version.get("server_version"),
        "uid": uid, "user": user["name"], "login": user["login"],
        "api_key": mask(api_key),
        "mode": "lecture seule" if c.readonly else "LECTURE + ÉCRITURE",
    })


@mcp.tool()
def odoo_status() -> str:
    """État de la connexion courante : base, utilisateur, version, mode lecture/écriture."""
    c = _get_client()
    uid = c.uid
    user = c.read("res.users", [uid], ["name", "login"])[0]
    etat = {
        "url": c.url, "db": c.db, "uid": uid,
        "user": user["name"], "login": user["login"],
        "api_key": mask(c.api_key),
        "server_version": c.version().get("server_version"),
        "mode": "lecture seule" if c.readonly else "LECTURE + ÉCRITURE",
    }
    if c.journal:
        etat["journal"] = {
            "fichier": str(c.journal.path),
            "titre": c.journal.titre,
            "chapitre_courant": c.journal.chapitre or "(aucun)",
            **c.journal.synthese(),
        }
    else:
        etat["journal"] = ("aucun — ouvre-en un avec odoo_journal_start pour tracer "
                           "les écritures et pouvoir produire un rapport")
    etat["mode_demonstration"] = (
        {"actif": True, "domaine": c.domaine_demo,
         "adresses_neutralisees": c.emails_neutralises}
        if c.mode_demo else
        "inactif — à activer (odoo_demo_mode) avant de générer des données de "
        "démonstration, sinon de vrais courriels peuvent partir")
    etat["budget_contexte"] = _budget.etat()
    return _j(etat)


@mcp.tool()
def odoo_enable_write(enable: bool = True) -> str:
    """Activer (ou couper) l'ecriture. A n'appeler qu'apres avoir montre a l'utilisateur
    ce qui va etre modifie et obtenu son accord. Les suppressions sont irreversibles.
    """
    c = _get_client()
    c.readonly = not enable
    return _j({"mode": "LECTURE + ÉCRITURE" if enable else "lecture seule"})


# --------------------------------------------------------------------- lecture
@mcp.tool()
def odoo_models(name_contains: str = "", limit: int = 100) -> str:
    """Lister les modèles disponibles (res.partner, sale.order, ...)."""
    c = _get_client()
    domain = [["model", "like", name_contains]] if name_contains else []
    return _j(c.search_read("ir.model", domain, ["model", "name"],
                            limit=limit, order="model"))


@mcp.tool()
def odoo_fields(model: str, name_contains: str = "", writable_only: bool = False,
                detail: bool = False) -> str:
    """Décrire les champs d'un modèle. À faire AVANT toute écriture : les noms changent
    entre versions d'Odoo (en 19, `is_company` existe mais pas `company_type`).

    Renvoie par défaut une forme compacte — `"partner_id": "many2one>res.partner*"`, où
    `*` marque un champ obligatoire. Un modèle Odoo compte souvent 250 champs : la forme
    détaillée dépasse 20 000 tokens, alors que la compacte suffit à écrire correctement.

    Filtre avec `name_contains`, et n'active `detail` que pour les quelques champs dont tu
    as besoin des libellés ou des valeurs de sélection.
    """
    c = _get_client()
    f = c.fields_get(model)
    needle = name_contains.lower()
    retenus = {}
    for k in sorted(f):
        info = f[k]
        if needle and needle not in k.lower() \
                and needle not in str(info.get("string", "")).lower():
            continue
        if writable_only and (info.get("readonly") or info.get("store") is False):
            continue
        if not needle and not detail and k.startswith(PREFIXES_TECHNIQUES):
            continue
        retenus[k] = info

    if detail:
        return _j({k: {a: v[a] for a in ("string", "type", "relation", "required",
                                         "selection") if v.get(a)}
                   for k, v in retenus.items()})

    compact = {}
    for k, v in retenus.items():
        forme = v.get("type", "?")
        if v.get("relation"):
            forme += f">{v['relation']}"
        if v.get("required"):
            forme += "*"
        if v.get("readonly"):
            forme += " (lecture seule)"
        compact[k] = forme
    return _j({"model": model, "nb_champs": len(compact),
               "legende": "type>relation, * = obligatoire",
               "champs": compact})


@mcp.tool()
def odoo_search(model: str, domain: str = "[]", fields: str = "[]",
                limit: int = 0, offset: int = 0, order: str = "") -> str:
    """Rechercher et lire des enregistrements. Renvoie aussi le total correspondant au
    domaine, ce qui évite un appel odoo_count separe.

    `domain` et `fields` sont des tableaux JSON :
    domain='[["customer_rank",">",0]]'  fields='["name","email"]'.

    **Precise toujours `fields`** : sans lui, une poignee de champs courants est choisie
    d'office, ce qui n'est presque jamais exactement ce dont tu as besoin.
    """
    c = _get_client()
    dom = _parse(domain, [])
    champs = _parse(fields, [])
    auto = not champs
    if auto:
        champs = _champs_par_defaut(c, model)
    limite = limit or _budget.limite_par_defaut

    total = c.search_count(model, dom)
    lignes = c.search_read(model, dom, champs,
                           limit=limite, offset=offset, order=order or None)

    reponse: dict = {"model": model, "total_correspondant": total,
                     "affiches": len(lignes)}
    if auto:
        reponse["champs_choisis_automatiquement"] = champs
        reponse["conseil"] = ("Rappelle avec `fields` pour obtenir exactement les "
                              "champs voulus.")
    if total > offset + len(lignes):
        reponse["suite"] = f"offset={offset + len(lignes)} pour la suite"
    reponse["resultats"] = lignes
    return _j(reponse)


@mcp.tool()
def odoo_count(model: str, domain: str = "[]") -> str:
    """Compter les enregistrements correspondant au domaine."""
    c = _get_client()
    return _j({"model": model, "count": c.search_count(model, _parse(domain, []))})


@mcp.tool()
def odoo_read(model: str, ids: str, fields: str = "[]") -> str:
    """Lire des enregistrements par identifiants. `ids` est un tableau JSON, ex. '[1,2]'."""
    c = _get_client()
    return _j(c.read(model, _parse(ids, []), _parse(fields, [])))


@mcp.tool()
def odoo_aggregate(model: str, groupby: str, domain: str = "[]", measures: str = "") -> str:
    """Nombre et somme(s) par valeur de `groupby`. Remplace read_group, retire du RPC
    depuis Odoo 18. `measures` : liste JSON de champs a sommer. Sur une date, granularite
    `date_order:month` (aussi day/week/quarter/year) — pour un CA par mois.
    """
    c = _get_client()
    parsed = _parse(measures, [])
    if isinstance(parsed, str):
        parsed = [parsed]
    return _j(c.aggregate(model, _parse(domain, []), groupby, parsed))


@mcp.tool()
def odoo_name_find(model: str, name: str, limit: int = 10) -> str:
    """Retrouver des enregistrements par leur nom (autocompletion Odoo). Le plus rapide
    pour convertir "le client Polytec" en identifiant avant d'ecrire.
    """
    c = _get_client()
    found = c.execute_kw(model, "name_search", [name], {"limit": limit})
    return _j([{"id": i, "nom": n} for i, n in found])


# -------------------------------------------------------------------- écriture
@mcp.tool()
def odoo_create(model: str, values: str) -> str:
    """Créer un enregistrement. `values` est un objet JSON, ex. '{"name":"ACME"}'.

    Bloqué tant que odoo_enable_write n'a pas été appelé.
    """
    c = _get_client()
    _require_write(c)
    new_id = c.create(model, _parse(values, {}))
    return _j({"model": model, "created_id": new_id})


@mcp.tool()
def odoo_write(model: str, ids: str, values: str) -> str:
    """Modifier des enregistrements. `ids` tableau JSON, `values` objet JSON.

    Bloqué tant que odoo_enable_write n'a pas été appelé.
    """
    c = _get_client()
    _require_write(c)
    ok = c.write(model, _parse(ids, []), _parse(values, {}))
    return _j({"model": model, "ids": _parse(ids, []), "written": ok})


@mcp.tool()
def odoo_unlink(model: str, ids: str, confirm_bulk: bool = False) -> str:
    """Supprimer definitivement. IRREVERSIBLE, aucune corbeille. Au-dela de 50, exige
    `confirm_bulk`. Archiver ({"active": false}) est souvent preferable a supprimer.
    """
    c = _get_client()
    _require_write(c)
    id_list = _parse(ids, [])
    if len(id_list) > 50 and not confirm_bulk:
        noms = c.read(model, id_list[:5], ["display_name"])
        raise OdooError(
            f"{len(id_list)} suppressions demandées sur {model}. Montre cet échantillon "
            f"à l'utilisateur — {[n.get('display_name') for n in noms]} — obtiens son "
            "accord, puis rappelle avec confirm_bulk=true."
        )
    ok = c.unlink(model, id_list)
    return _j({"model": model, "supprimes": len(id_list), "deleted": ok})


@mcp.tool()
def odoo_upsert(xmlid: str, model: str, values: str) -> str:
    """Creer ou mettre a jour par External ID (ex. 'monprojet.client_acme'). Rejouable
    sans doublon — a preferer a odoo_create pour toute donnee de maquette ou d'import.
    """
    c = _get_client()
    _require_write(c)
    rec_id = c.ensure(xmlid, model, _parse(values, {}))
    return _j({"model": model, "xmlid": xmlid, "id": rec_id})


@mcp.tool()
def odoo_execute(model: str, method: str, args: str = "[]", kwargs: str = "{}") -> str:
    """Appeler n'importe quelle méthode d'un modèle (execute_kw brut).

    Les méthodes d'écriture (create, write, unlink, load, action_confirm, ...) restent
    bloquées tant que odoo_enable_write n'a pas été appelé.
    """
    c = _get_client()
    return _j(c.execute_kw(model, method, _parse(args, []), _parse(kwargs, {})))


@mcp.tool()
def odoo_update_where(model: str, domain: str, values: str, confirm: bool = False,
                      max_records: int = 500, motif: str = "") -> str:
    """Modifier en masse les enregistrements d'un domaine.

    Sans `confirm`, ne modifie RIEN : renvoie le nombre concerne et un echantillon
    avant/apres. Montre-le a l'utilisateur, obtiens son accord, puis rappelle avec
    confirm=True — garde-fou contre un domaine mal ecrit.

    `motif` justifie la modification dans le rapport d'intervention.
    """
    c = _get_client()
    dom = _parse(domain, [])
    vals = _parse(values, {})
    if not vals:
        raise OdooError("`values` est vide : rien à modifier.")

    ids = c.execute_kw(model, "search", [dom], {"limit": max_records + 1})
    if len(ids) > max_records:
        raise OdooError(
            f"{len(ids)}+ enregistrements visés, au-delà de la limite de {max_records}. "
            "Restreins le domaine, ou relance en augmentant max_records en connaissance "
            "de cause."
        )
    if not ids:
        return _j({"model": model, "concernes": 0, "message": "Aucun enregistrement."})

    champs = sorted(vals)
    avant = c.read(model, ids[:5], ["display_name"] + champs)
    apercu = [{"id": r["id"], "nom": r.get("display_name"),
               "avant": {k: files.flatten(r.get(k)) for k in champs},
               "apres": vals} for r in avant]

    if not confirm:
        return _j({
            "mode": "PREVISUALISATION - rien n'a ete modifie",
            "model": model, "concernes": len(ids), "modifications": vals,
            "apercu": apercu,
            "suite": "Fais valider ces changements par l'utilisateur, puis rappelle "
                     "odoo_update_where avec confirm=true.",
        })

    _require_write(c)
    precedent = c.motif_courant
    if motif:
        c.motif_courant = motif
    try:
        c.write(model, ids, vals)
    finally:
        c.motif_courant = precedent
    return _j({"model": model, "modifies": len(ids), "modifications": vals,
               "motif": motif or precedent})


@mcp.tool()
def odoo_import_file(path: str, mode: str = "inspect", model: str = "",
                     mapping: str = "{}", sheet: str = "", header_row: int = 1,
                     batch_size: int = 200) -> str:
    """Importer un fichier Excel/CSV local. Le serveur lit le fichier sur disque :
    aucune limite de volume.

    Modes a enchainer : inspect (structure, sans connexion), check (valide le mapping
    sans ecrire), run (importe par lots via load(), atomique par lot).

    `mapping` relie colonnes et champs — les en-tetes des exports Odoo sont des libelles
    d'interface, pas des noms de champs :
    {"_model":"res.partner","_columns":{"Code":"id","Nom":"name","Notes":null},
     "_constants":{"is_company":"True"},"_replace":{"type":{"Goods":"consu"}}}
    Mapper une colonne sur 'id' rend l'import rejouable sans doublon.
    """
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise OdooError(f"Fichier introuvable : {file_path}")
    header, rows = files.read_rows(file_path, sheet, header_row)

    if mode == "inspect":
        return _j({"fichier": file_path.name, **files.inspect_summary(header, rows)})

    m = _parse(mapping, {})
    target = model or m.get("_model")
    if not target:
        raise OdooError("Précise `model`, ou '_model' dans le mapping.")
    fields, data = files.build(header, rows, m)
    if not fields:
        raise OdooError("Aucune colonne mappée : vérifie '_columns' au regard des "
                        "en-têtes retournés par le mode 'inspect'.")

    c = _get_client()
    connus = c.fields_get(target)
    base = {f.split("/")[0] for f in fields if f != "id"}
    inconnus = sorted(f for f in base if f not in connus)
    lecture_seule = sorted(f for f in base
                           if connus.get(f, {}).get("readonly")
                           and connus.get(f, {}).get("store") is not False)

    rapport = {
        "fichier": file_path.name, "model": target,
        "lignes": len(data), "champs": fields,
        "champs_inconnus": inconnus,
        "champs_lecture_seule": lecture_seule,
        "apercu": [dict(zip(fields, row)) for row in data[:3]],
    }

    if mode == "check":
        rapport["verdict"] = ("Des champs sont invalides : corrige le mapping."
                              if inconnus else "Prêt à importer (mode 'run').")
        return _j(rapport)

    if mode != "run":
        raise OdooError(f"Mode inconnu : {mode!r} (attendu inspect, check ou run)")
    if inconnus:
        raise OdooError(f"Champs inconnus sur {target} : {', '.join(inconnus)}. "
                        "Lance le mode 'check' et corrige le mapping.")

    _require_write(c)
    importes = 0
    for start in range(0, len(data), batch_size):
        chunk = data[start:start + batch_size]
        try:
            res = c.load(target, fields, chunk)
        except OdooError as exc:
            raise OdooError(
                f"Échec sur le lot {start}-{start + len(chunk)} après {importes} lignes "
                f"importées. Ce lot n'a rien écrit (load() est atomique).\n{exc}"
            ) from exc
        importes += len(res.get("ids") or [])
    rapport["importes"] = importes
    rapport["verdict"] = "Import terminé."
    return _j(rapport)


@mcp.tool()
def odoo_export_file(model: str, path: str, domain: str = "[]", fields: str = "[]",
                     limit: int = 10000, order: str = "") -> str:
    """Exporter une recherche vers un .xlsx ou .csv local. Les relations sont aplaties
    (un many2one devient son libelle). Sans `fields`, prend les champs les plus courants.
    """
    c = _get_client()
    out_path = Path(path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cols = _parse(fields, [])
    if not cols:
        meta = c.fields_get(model)
        cols = [f for f in ("display_name", "name", "default_code", "partner_id", "date",
                            "date_order", "state", "amount_untaxed", "amount_total",
                            "email", "phone", "city", "country_id")
                if f in meta]
        if not cols:
            cols = ["display_name"]

    rows = c.search_read(model, _parse(domain, []), cols,
                         limit=limit, order=order or None)
    table = [[files.flatten(r.get(col)) for col in cols] for r in rows]
    files.write_table(out_path, cols, table)
    return _j({"fichier": str(out_path), "model": model,
               "lignes": len(rows), "colonnes": cols})


@mcp.tool()
def odoo_get_attachment(attachment_id: int, path: str = "") -> str:
    """Telecharger une piece jointe (ir.attachment) sur le disque. Pour trouver son id :
    odoo_search sur ir.attachment avec '[["res_model","=","account.move"],["res_id","=",42]]'.
    """
    c = _get_client()
    meta = c.read("ir.attachment", [attachment_id], ["name", "mimetype"])
    if not meta:
        raise OdooError(f"Pièce jointe {attachment_id} introuvable.")
    meta = meta[0]

    # Le champ du contenu a changé de nom : 'raw' depuis Odoo 16, 'datas' avant.
    contenu = None
    for champ in ("raw", "datas"):
        try:
            rec = c.read("ir.attachment", [attachment_id], [champ])
        except OdooError:
            continue
        contenu = rec[0].get(champ)
        if contenu:
            break
    if not contenu:
        raise OdooError(
            f"La pièce jointe '{meta['name']}' n'a pas de contenu lisible "
            "(lien externe, ou stockage inaccessible via XML-RPC)."
        )

    brut = contenu.data if hasattr(contenu, "data") else contenu
    octets = base64.b64decode(brut) if isinstance(brut, str) else bytes(brut)

    out_path = Path(path).expanduser() if path else Path.cwd() / meta["name"]
    if out_path.is_dir():
        out_path = out_path / meta["name"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(octets)
    return _j({"fichier": str(out_path), "nom": meta["name"],
               "type": meta.get("mimetype"), "octets": out_path.stat().st_size})


# --------------------------------------------------------------------- journal
def _journal() -> Journal:
    c = _get_client()
    if c.journal is None:
        raise OdooError(
            "Aucun journal ouvert. Appelle odoo_journal_start(titre, objectif) au début "
            "de la session : toutes les écritures seront alors tracées automatiquement, "
            "avec leur état avant/après, et un rapport présentable pourra être généré."
        )
    return c.journal


@mcp.tool()
def odoo_journal_start(titre: str, objectif: str = "", path: str = "") -> str:
    """Ouvrir un journal d'intervention — a faire AVANT toute ecriture.

    Chaque ecriture est ensuite tracee automatiquement, avec l'etat AVANT modification,
    ce qui permet de produire un rapport expliquant ce qui a change et pourquoi.
    `titre` et `objectif` figurent en tete du rapport remis au client.
    """
    c = _get_client()
    uid = c.uid
    utilisateur = c.read("res.users", [uid], ["name"])[0]["name"]
    if path:
        chemin = Path(path).expanduser()
    else:
        horodatage = datetime.now().strftime("%Y%m%d-%H%M")
        chemin = DOSSIER_JOURNAUX / f"{c.db}_{horodatage}.jsonl"
    c.journal = Journal(chemin, base=c.db, utilisateur=utilisateur,
                        titre=titre, objectif=objectif)
    return _j({"journal": str(chemin), "titre": titre, "objectif": objectif,
               "base": c.db, "utilisateur": utilisateur,
               "suite": "Les écritures sont désormais tracées. Ouvre une étape avec "
                        "odoo_journal_chapter avant chaque phase de travail."})


@mcp.tool()
def odoo_journal_chapter(nom: str, pourquoi: str = "") -> str:
    """Ouvrir une etape de travail dans le journal : les operations suivantes y sont
    rattachees. `pourquoi` est la justification metier — c'est elle qui repond a
    "pourquoi cette modification ?" dans le rapport.
    """
    jr = _journal()
    jr.ouvrir_chapitre(nom, pourquoi)
    _get_client().motif_courant = pourquoi
    return _j({"chapitre": nom, "pourquoi": pourquoi})


@mcp.tool()
def odoo_journal_note(texte: str, categorie: str = "note") -> str:
    """Consigner dans le journal ce qui n'est pas une ecriture mais merite d'etre
    explique au client : arbitrage, anomalie, limite rencontree, sujet ecarte.
    `categorie` : note, decision ou alerte.
    """
    jr = _journal()
    jr.noter(texte, categorie)
    return _j({"enregistre": texte, "categorie": categorie})


@mcp.tool()
def odoo_journal_report(path: str = "", format: str = "html",
                        journal_path: str = "") -> str:
    """Rapport d'intervention : ce qui a ete fait et pourquoi. Document autonome,
    presentable au client — synthese, deroule par etape, detail avant/apres.

    `format` : html, markdown ou both. `journal_path` pour rejouer une session passee.
    """
    if journal_path:
        chemin = Path(journal_path).expanduser()
        session, entrees = Journal.charger(chemin)
        temporaire = Journal.__new__(Journal)
        temporaire.entrees = entrees
        synthese = Journal.synthese(temporaire)
    else:
        jr = _journal()
        chemin = jr.path
        session = {"titre": jr.titre, "objectif": jr.objectif, "base": jr.base,
                   "utilisateur": jr.utilisateur,
                   "ts": jr.debut.isoformat(timespec="seconds")}
        entrees, synthese = jr.entrees, jr.synthese()

    base_out = Path(path).expanduser() if path else chemin.with_suffix("")
    if base_out.is_dir():
        base_out = base_out / chemin.stem
    base_out.parent.mkdir(parents=True, exist_ok=True)

    produits = []
    if format in ("html", "both"):
        p = base_out.with_suffix(".html")
        p.write_text(rendre_html(session, entrees, synthese), encoding="utf-8")
        produits.append(str(p))
    if format in ("markdown", "md", "both"):
        p = base_out.with_suffix(".md")
        p.write_text(rendre_markdown(session, entrees, synthese), encoding="utf-8")
        produits.append(str(p))
    if not produits:
        raise OdooError(f"Format inconnu : {format!r} (attendu html, markdown ou both)")

    return _j({"rapports": produits, "journal": str(chemin), "synthese": synthese,
               "sans_motif": f"{synthese['sans_motif']} opération(s) sans justification "
                             "— pense à ouvrir une étape (odoo_journal_chapter) avant "
                             "d'écrire." if synthese["sans_motif"] else ""})


@mcp.tool()
def odoo_presentation_guide(path: str = "", format: str = "html",
                            journal_path: str = "") -> str:
    """Generer le deroule de demonstration a suivre en reunion, ecran par ecran.

    Deduit de ce qui a reellement ete fait. Chaque etape donne le chemin de menu exact,
    les clics en cases a cocher, les enregistrements a ouvrir et une phrase d'accroche.
    """
    if journal_path:
        chemin = Path(journal_path).expanduser()
        session, entrees = Journal.charger(chemin)
        temporaire = Journal.__new__(Journal)
        temporaire.entrees = entrees
        synthese = Journal.synthese(temporaire)
    else:
        jr = _journal()
        chemin = jr.path
        session = {"titre": jr.titre, "objectif": jr.objectif, "base": jr.base,
                   "utilisateur": jr.utilisateur,
                   "ts": jr.debut.isoformat(timespec="seconds")}
        entrees, synthese = jr.entrees, jr.synthese()

    # Quels objets ont été touchés, et sous quels noms — pour pouvoir les retrouver en démo.
    models: set[str] = set()
    exemples: dict[str, list[str]] = {}
    for e in entrees:
        if e.get("type") != "operation" or e.get("methode") == "unlink":
            continue
        models.add(e["model"])
        noms = [str(a.get("display_name")) for a in (e.get("avant") or [])
                if a.get("display_name")]
        if not noms and isinstance(e.get("apres"), dict):
            valeur = e["apres"].get("name") or e["apres"].get("display_name")
            if valeur:
                noms = [str(valeur)]
        if noms:
            exemples.setdefault(e["model"], []).extend(noms)

    guide = presentation.construire(models, session, exemples)
    if not guide:
        raise OdooError("Rien à présenter : aucune création ou modification enregistrée "
                        "dans ce journal.")

    base_out = Path(path).expanduser() if path else chemin.with_name(
        chemin.stem + "_presentation")
    if base_out.is_dir():
        base_out = base_out / (chemin.stem + "_presentation")
    base_out.parent.mkdir(parents=True, exist_ok=True)

    produits = []
    if format in ("html", "both"):
        p = base_out.with_suffix(".html")
        p.write_text(presentation.rendre_html(session, guide, synthese), encoding="utf-8")
        produits.append(str(p))
    if format in ("markdown", "md", "both"):
        p = base_out.with_suffix(".md")
        p.write_text(presentation.rendre_markdown(session, guide, synthese),
                     encoding="utf-8")
        produits.append(str(p))
    if not produits:
        raise OdooError(f"Format inconnu : {format!r} (attendu html, markdown ou both)")

    return _j({
        "guides": produits,
        "etapes": [{"n": i, "titre": e["titre"], "ou": e["chemin"]}
                   for i, e in enumerate(guide, 1)],
        "conseil": "Ouvre le fichier HTML pendant la réunion : les cases à cocher "
                   "permettent de suivre le déroulé sans rien oublier.",
    })


@mcp.tool()
def odoo_recent_changes(model: str, jours: int = 7, limit: int = 50,
                        domain: str = "[]") -> str:
    """Ce qui a bouge recemment dans un modele, d'apres l'audit natif d'Odoo
    (write_date, write_uid). Voit AUSSI les modifications faites dans l'interface par
    d'autres personnes, contrairement au journal du connecteur.
    """
    c = _get_client()
    depuis = (datetime.now() - timedelta(days=jours)).strftime("%Y-%m-%d %H:%M:%S")
    dom = _parse(domain, []) + [["write_date", ">=", depuis]]
    champs = [f for f in ("display_name", "create_date", "write_date",
                          "create_uid", "write_uid")
              if f in c.fields_get(model)]
    recs = c.search_read(model, dom, champs, limit=limit, order="write_date desc")
    lignes = []
    for r in recs:
        cree = str(r.get("create_date", ""))[:19]
        modifie = str(r.get("write_date", ""))[:19]
        lignes.append({
            "id": r["id"],
            "nom": r.get("display_name"),
            "etat": "créé" if cree[:16] == modifie[:16] else "modifié",
            "le": modifie,
            "par": files.flatten(r.get("write_uid")),
        })
    return _j({"model": model, "depuis": depuis, "nb": len(lignes),
               "changements": lignes})


# --------------------------------------------------- maquettes de démonstration
@mcp.tool()
def odoo_demo_questionnaire() -> str:
    """Questionnaire de qualification a faire remplir avant de batir une demo prospect.

    Presente-le tel quel, preambule compris. N'invente pas les reponses manquantes :
    signale tes hypotheses. Ensuite compose le plan de maquette et fais-le valider
    AVANT d'ecrire. Les consignes de composition accompagnent la reponse.
    """
    return _j({
        "questionnaire": demo.QUESTIONNAIRE,
        "consignes_de_composition": demo.CONSIGNES_GENERATION,
        "avant_d_ecrire": "Activer odoo_demo_mode pour neutraliser les adresses e-mail, "
                          "puis odoo_journal_start pour tracer la génération.",
    })


@mcp.tool()
def odoo_demo_mode(actif: bool = True, domaine: str = "example.com") -> str:
    """Filet de securite e-mail — a activer AVANT toute generation de donnees de demo.

    Les bases de demo ne sont pas neutralisees : Odoo envoie de vrais courriels a la
    confirmation d'une commande ou d'une facture. Une fois actif, toute adresse ecrite
    par n'importe quel outil est reecrite vers `example.com` (domaine reserve RFC 2606).
    La partie gauche est conservee, l'adresse reste lisible en demo.
    """
    c = _get_client()
    c.mode_demo = actif
    c.domaine_demo = domaine or "example.com"
    if actif:
        c.emails_neutralises = 0
    return _j({
        "mode_demonstration": "actif" if actif else "inactif",
        "domaine_de_neutralisation": c.domaine_demo if actif else None,
        "portee": "toutes les écritures, quel que soit l'outil utilisé"
                  if actif else "aucune",
    })


@mcp.tool()
def odoo_demo_check(corriger: bool = False, limit: int = 2000) -> str:
    """Verifier qu'aucune adresse reelle ne subsiste avant de demontrer. Balaie contacts
    et salaries. A lancer sur une base reprise de quelqu'un d'autre. Avec `corriger`,
    les adresses trouvees sont neutralisees.
    """
    c = _get_client()
    domaine = c.domaine_demo or "example.com"
    suspects = []
    for model, champ in (("res.partner", "email"), ("hr.employee", "work_email")):
        try:
            recs = c.search_read(model, [[champ, "!=", False]],
                                 ["display_name", champ], limit=limit)
        except OdooError:
            continue
        for r in recs:
            if not demo.domaine_sur(r.get(champ) or "", domaine):
                suspects.append({"model": model, "id": r["id"],
                                 "nom": r.get("display_name"),
                                 "email": r.get(champ), "champ": champ})

    if not suspects:
        return _j({"verdict": "Aucune adresse réelle détectée — la base est sûre.",
                   "domaine_attendu": domaine})

    if not corriger:
        return _j({
            "verdict": f"{len(suspects)} adresse(s) pourraient recevoir du courrier réel.",
            "risque": "Confirmer une commande ou une facture enverrait un vrai courriel.",
            "exemples": suspects[:15],
            "suite": "Relance avec corriger=true pour les neutraliser.",
        })

    _require_write(c)
    corrigees = 0
    for s in suspects:
        c.write(s["model"], [s["id"]],
                {s["champ"]: demo.neutraliser(s["email"], domaine)})
        corrigees += 1
    return _j({"verdict": f"{corrigees} adresse(s) neutralisée(s) vers @{domaine}.",
               "base": "sûre pour la démonstration"})


# ---------------------------------------------------- tableaux de bord Odoo
@mcp.tool()
def odoo_dashboard_list() -> str:
    """Lister les tableaux de bord et leurs rubriques — pour savoir ou ranger un nouveau
    tableau de bord, ou reperer ceux d'Odoo dont s'inspirer.
    """
    c = _get_client()
    groupes = c.search_read("spreadsheet.dashboard.group", [], ["name", "sequence"],
                            order="sequence")
    tableaux = c.search_read("spreadsheet.dashboard", [],
                             ["name", "dashboard_group_id", "sequence", "is_published"],
                             order="sequence", limit=200)
    par_groupe: dict[str, list] = {}
    for t in tableaux:
        g = t["dashboard_group_id"][1] if t["dashboard_group_id"] else "(sans rubrique)"
        par_groupe.setdefault(g, []).append(
            {"id": t["id"], "nom": t["name"], "publie": t["is_published"]})
    return _j({
        "rubriques": [{"id": g["id"], "nom": g["name"]} for g in groupes],
        "tableaux_de_bord": par_groupe,
        "ou_les_voir": "Menu « Tableaux de bord » dans Odoo",
    })


@mcp.tool()
def odoo_dashboard_inspect(dashboard_id: int) -> str:
    """Decrire le contenu d'un tableau de bord (graphiques, sources, filtres) en clair.
    Pratique pour s'inspirer d'un tableau de bord Odoo existant.
    """
    c = _get_client()
    rec = c.read("spreadsheet.dashboard", [dashboard_id],
                 ["name", "dashboard_group_id", "spreadsheet_binary_data"])
    if not rec:
        raise OdooError(f"Tableau de bord {dashboard_id} introuvable.")
    rec = rec[0]
    if not rec.get("spreadsheet_binary_data"):
        raise OdooError(f"« {rec['name']} » n'a pas de contenu lisible.")
    classeur = dashboards.decoder(rec["spreadsheet_binary_data"])
    return _j({
        "nom": rec["name"],
        "rubrique": rec["dashboard_group_id"][1] if rec["dashboard_group_id"] else None,
        **dashboards.resumer(classeur),
    })


@mcp.tool()
def odoo_dashboard_create(nom: str, graphiques: str, rubrique: str = "",
                          sous_titre: str = "", dashboard_id: int = 0) -> str:
    """Creer (ou remplacer via `dashboard_id`) un tableau de bord Odoo. Les graphiques
    sont recalcules en direct par Odoo, ce ne sont pas des images.

    `graphiques` est une liste JSON :
    {"titre":"CA par mois","model":"sale.order","groupby":["date_order:month"],
     "mesure":"amount_untaxed","type":"line","domaine":[["state","=","sale"]],
     "pleine_largeur":true}
    type = bar|line|pie ; mesure "__count" pour compter ; sur une date, granularite
    `champ:month` (aussi day/week/quarter/year).

    Modele, champs et mesure sont valides avant ecriture — la validation fonctionne en
    lecture seule. `rubrique` = rubrique de menu, creee si absente.
    """
    c = _get_client()

    specs = _parse(graphiques, [])
    if isinstance(specs, dict):
        specs = [specs]
    if not specs:
        raise OdooError("Aucun graphique fourni : `graphiques` est une liste JSON.")

    # Validation d'abord : elle fonctionne même en lecture seule, ce qui permet de
    # vérifier une maquette de tableau de bord avant de demander l'autorisation d'écrire.
    problemes = dashboards.valider(c, specs)
    if problemes:
        raise OdooError("Le tableau de bord n'a pas été créé — à corriger d'abord :\n  "
                        + "\n  ".join(problemes))

    _require_write(c)
    classeur = dashboards.construire(nom, specs, sous_titre)
    donnees = dashboards.encoder(classeur)

    if dashboard_id:
        c.write("spreadsheet.dashboard", [dashboard_id],
                {"name": nom, "spreadsheet_binary_data": donnees})
        cible = dashboard_id
        action = "remplacé"
    else:
        nom_rubrique = rubrique or "Analyses"
        trouve = c.search_read("spreadsheet.dashboard.group",
                               [["name", "=", nom_rubrique]], ["id"], limit=1)
        groupe = trouve[0]["id"] if trouve else c.create(
            "spreadsheet.dashboard.group", {"name": nom_rubrique})
        cible = c.create("spreadsheet.dashboard", {
            "name": nom,
            "dashboard_group_id": groupe,
            "spreadsheet_binary_data": donnees,
            "is_published": True,
        })
        action = "créé"

    resume = []
    for g in specs:
        resume.append(f"{g.get('titre') or '(sans titre)'} — "
                      f"{dashboards.TYPES.get((g.get('type') or 'bar').lower(), '')}")
    return _j({
        "id": cible,
        "nom": nom,
        "statut": f"tableau de bord {action}",
        "graphiques": resume,
        "ou_le_voir": f"Menu « Tableaux de bord » → {rubrique or 'Analyses'} → {nom}",
    })


@mcp.tool()
def odoo_saved_analysis(nom: str, model: str, groupby: str = "[]", mesures: str = "[]",
                        domaine: str = "[]", vue: str = "pivot",
                        partage: bool = True) -> str:
    """Enregistrer un favori Odoo : une analyse que le client retrouve dans le menu des
    filtres de la vue, regroupement et mesures deja en place. Plus leger qu'un tableau
    de bord — le bon reflexe pour une analyse ponctuelle qu'il rejouera seul.

    `vue` : pivot ou graph. `partage` la rend visible par tous.
    """
    c = _get_client()
    _require_write(c)

    champs = c.fields_get(model)
    gb = [str(x) for x in _parse(groupby, [])]
    ms = [str(x) for x in _parse(mesures, [])]
    inconnus = [x.split(":")[0] for x in gb + ms
                if x.split(":")[0] not in champs and x != "__count"]
    if inconnus:
        raise OdooError(f"Champs inconnus sur {model} : {', '.join(inconnus)}")

    contexte = {
        "group_by": gb,
        "pivot_measures": ms or ["__count"],
        "pivot_row_groupby": gb[:1],
        "pivot_column_groupby": gb[1:2],
    }
    filtre_id = c.create("ir.filters", {
        "name": nom,
        "model_id": model,
        "domain": json.dumps(_parse(domaine, [])),
        "context": json.dumps(contexte),
        "user_ids": [] if partage else [(6, 0, [c.uid])],
    })
    return _j({
        "id": filtre_id,
        "nom": nom,
        "statut": "analyse enregistrée",
        "ou_la_voir": f"Ouvrir la vue {vue} de « {model} », puis menu « Favoris » → {nom}",
        "partagee": partage,
    })


def main() -> None:
    """Point d'entrée du binaire `odoo-mcp` (transport stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
