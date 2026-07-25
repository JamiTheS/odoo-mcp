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

from odoo_mcp import files, presentation
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


def _j(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


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
    """Se connecter à une base Odoo. À appeler en premier si aucune connexion n'est active.

    Les identifiants sont gardés en mémoire du serveur uniquement — jamais écrits sur
    disque. `db` se déduit du sous-domaine si omis (Odoo Online). Laisser
    `allow_write` à False : l'écriture s'active séparément via odoo_enable_write.
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
    return _j(etat)


@mcp.tool()
def odoo_enable_write(enable: bool = True) -> str:
    """Activer (ou couper) l'écriture pour la session.

    À n'appeler qu'après avoir montré à l'utilisateur ce qui va être modifié et obtenu
    son accord explicite. Les suppressions Odoo sont irréversibles.
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
def odoo_fields(model: str, name_contains: str = "", writable_only: bool = False) -> str:
    """Décrire les champs d'un modèle. À faire AVANT toute écriture : les noms de champs
    changent entre versions d'Odoo (ex. en 19, is_company existe mais pas company_type)."""
    c = _get_client()
    f = c.fields_get(model)
    out = {}
    needle = name_contains.lower()
    for k in sorted(f):
        if needle and needle not in k.lower() \
                and needle not in str(f[k].get("string", "")).lower():
            continue
        if writable_only and (f[k].get("readonly") or f[k].get("store") is False):
            continue
        out[k] = {a: f[k][a] for a in ("string", "type", "relation", "required", "selection")
                  if f[k].get(a)}
    return _j(out)


@mcp.tool()
def odoo_search(model: str, domain: str = "[]", fields: str = "[]",
                limit: int = 50, offset: int = 0, order: str = "") -> str:
    """Rechercher et lire des enregistrements.

    `domain` et `fields` sont des tableaux JSON :
    domain='[["customer_rank",">",0]]'  fields='["name","email"]'.
    """
    c = _get_client()
    return _j(c.search_read(model, _parse(domain, []), _parse(fields, []),
                            limit=limit, offset=offset, order=order or None))


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
    """Agréger : nombre et somme(s) par valeur de `groupby`.

    Remplace read_group, qui n'est plus exposé en RPC depuis Odoo 18.
    `measures` est une liste JSON de champs à sommer, ex. '["amount_untaxed","amount_total"]'.
    `groupby` accepte une granularité sur les dates — 'date_order:month' (aussi day, week,
    quarter, year) : c'est ainsi qu'on obtient un chiffre d'affaires par mois.
    """
    c = _get_client()
    parsed = _parse(measures, [])
    if isinstance(parsed, str):
        parsed = [parsed]
    return _j(c.aggregate(model, _parse(domain, []), groupby, parsed))


@mcp.tool()
def odoo_name_find(model: str, name: str, limit: int = 10) -> str:
    """Retrouver des enregistrements par leur nom, comme le ferait l'autocomplétion Odoo.

    Le plus rapide pour convertir « le client Polytec » en identifiant avant d'écrire.
    Cherche aussi sur les champs alternatifs du modèle (référence, code...).
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
    """Supprimer définitivement des enregistrements. IRRÉVERSIBLE — aucune corbeille.

    Bloqué tant que odoo_enable_write n'a pas été appelé. Au-delà de 50 enregistrements,
    exige `confirm_bulk` : montre d'abord à l'utilisateur ce qui va disparaître.
    Souvent, archiver (`odoo_write` avec {"active": false}) est préférable à supprimer.
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
    """Créer ou mettre à jour un enregistrement identifié par un External ID
    (ex. 'monprojet.client_acme'). Rejouable sans jamais créer de doublon —
    à préférer à odoo_create pour toute donnée de maquette ou d'import.

    Bloqué tant que odoo_enable_write n'a pas été appelé.
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
    """Modifier en masse tous les enregistrements correspondant à un domaine.

    Sans `confirm`, ne modifie RIEN : renvoie le nombre d'enregistrements concernés et
    un échantillon avant/après. Montre ce résultat à l'utilisateur, obtiens son accord,
    puis rappelle avec confirm=True. C'est le garde-fou qui évite d'écraser 800 fiches
    sur un domaine mal écrit.

    `motif` justifie la modification dans le rapport d'intervention — renseigne-le,
    c'est ce que le client lira pour comprendre pourquoi ses données ont changé.
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
    """Importer un fichier Excel/CSV local vers Odoo, en trois temps.

    Le serveur tourne sur la machine de l'utilisateur : il lit le fichier directement,
    sans le faire transiter par la conversation — c'est ce qui rend possible un import
    de plusieurs milliers de lignes.

    Modes, à enchaîner dans cet ordre :
      - 'inspect' : structure du fichier (colonnes, remplissage, valeurs distinctes,
        doublons d'identifiant). Aucune connexion requise.
      - 'check'   : construit les lignes et vérifie les champs contre le modèle,
        sans rien écrire.
      - 'run'     : importe réellement, par lots, via load() — l'import natif d'Odoo.

    `mapping` est un objet JSON reliant les colonnes du fichier aux champs Odoo. Les
    en-têtes des fichiers exportés d'Odoo sont des libellés d'interface ('Name*',
    'Sales Price'), jamais des noms de champs : ce mapping fait la traduction.

        {"_model": "res.partner",
         "_columns": {"Code": "id", "Nom": "name", "Pays": "country_id/id",
                      "Notes": null},
         "_constants": {"is_company": "True"},
         "_replace": {"type": {"Goods": "consu"}}}

    Mapper une colonne sur 'id' (External ID) rend l'import rejouable : une seconde
    exécution met à jour au lieu de dupliquer. load() rejette un lot entier en cas
    d'erreur, donc un échec ne laisse jamais de données à moitié écrites.
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
    """Exporter le résultat d'une recherche vers un fichier .xlsx ou .csv local.

    Le fichier est écrit directement sur la machine de l'utilisateur. Les relations
    sont aplaties pour rester lisibles : un many2one devient son libellé.
    Sans `fields`, exporte les champs stockés les plus courants du modèle.
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
    """Télécharger une pièce jointe Odoo (ir.attachment) sur le disque local.

    Utile pour récupérer un PDF de facture, un document lié à une affaire, une image.
    Pour retrouver l'identifiant : odoo_search sur ir.attachment avec un domaine du type
    '[["res_model","=","account.move"],["res_id","=",42]]'.
    Sans `path`, le fichier est écrit dans le dossier courant sous son nom d'origine.
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
    """Ouvrir un journal d'intervention. À faire AVANT toute écriture.

    Une fois ouvert, chaque création, modification, suppression et import est enregistré
    automatiquement — y compris l'état des enregistrements AVANT modification. C'est ce
    qui permet de produire ensuite un rapport expliquant ce qui a changé et pourquoi,
    sans avoir à retrouver l'information dans l'historique de la conversation.

    `titre` nomme l'intervention (« Maquette Pycarelle », « Reprise du catalogue »),
    `objectif` explique en une phrase ce qu'on cherche à obtenir : les deux figurent en
    tête du rapport remis au client.
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
    """Ouvrir une étape de travail dans le journal.

    Les opérations suivantes y sont rattachées, ce qui structure le rapport en phases
    lisibles (« Création du référentiel articles », « Correction des adresses »).
    `pourquoi` est la justification métier de l'étape : c'est elle qui répond à la
    question « pourquoi cette modification ? » dans le rapport.
    """
    jr = _journal()
    jr.ouvrir_chapitre(nom, pourquoi)
    _get_client().motif_courant = pourquoi
    return _j({"chapitre": nom, "pourquoi": pourquoi})


@mcp.tool()
def odoo_journal_note(texte: str, categorie: str = "note") -> str:
    """Consigner une décision, une observation ou un point d'attention dans le journal.

    À utiliser pour tout ce qui n'est pas une écriture mais mérite d'être expliqué au
    client : un arbitrage, une anomalie constatée, une limite technique rencontrée,
    un sujet laissé de côté. `categorie` peut valoir 'note', 'decision' ou 'alerte'.
    """
    jr = _journal()
    jr.noter(texte, categorie)
    return _j({"enregistre": texte, "categorie": categorie})


@mcp.tool()
def odoo_journal_report(path: str = "", format: str = "html",
                        journal_path: str = "") -> str:
    """Générer le rapport d'intervention : ce qui a été fait, et pourquoi.

    Produit un document autonome, présentable tel quel au client : synthèse chiffrée,
    volumes par modèle, déroulé chronologique par étape, et pour chaque modification
    le détail avant → après.

    `format` : 'html' (présentable, à ouvrir dans un navigateur ou imprimer en PDF),
    'markdown' (à intégrer dans un compte rendu), ou 'both'.
    `journal_path` permet de produire le rapport d'une session antérieure.
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
    """Générer le déroulé à suivre pour présenter le travail au client, écran par écran.

    Se fonde sur ce qui a réellement été fait : le guide ne contient que les étapes
    correspondant aux données mises en place. Pour chaque étape il donne le chemin de
    menu exact dans Odoo, les clics à faire sous forme de cases à cocher, les
    enregistrements précis à ouvrir, et une phrase d'accroche à dire au client.

    Pensé pour être ouvert pendant la réunion : on coche au fur et à mesure, on ne
    saute pas d'étape, et on garde le fil du discours.
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
    """Lister ce qui a bougé récemment dans un modèle, d'après Odoo lui-même.

    S'appuie sur les champs d'audit natifs (create_date, write_date, create_uid,
    write_uid) : contrairement au journal du connecteur, cet outil voit AUSSI les
    modifications faites directement dans l'interface Odoo par d'autres personnes.
    Utile pour reprendre le fil d'un dossier ou vérifier ce qui a changé depuis
    la dernière réunion.
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


def main() -> None:
    """Point d'entrée du binaire `odoo-mcp` (transport stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
