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

from odoo_mcp import files
from odoo_mcp.odoo_client import OdooClient, OdooError, ReadOnlyError, mask

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
    return _j({
        "url": c.url, "db": c.db, "uid": uid,
        "user": user["name"], "login": user["login"],
        "api_key": mask(c.api_key),
        "server_version": c.version().get("server_version"),
        "mode": "lecture seule" if c.readonly else "LECTURE + ÉCRITURE",
    })


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
                      max_records: int = 500) -> str:
    """Modifier en masse tous les enregistrements correspondant à un domaine.

    Sans `confirm`, ne modifie RIEN : renvoie le nombre d'enregistrements concernés et
    un échantillon avant/après. Montre ce résultat à l'utilisateur, obtiens son accord,
    puis rappelle avec confirm=True. C'est le garde-fou qui évite d'écraser 800 fiches
    sur un domaine mal écrit.
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
    c.write(model, ids, vals)
    return _j({"model": model, "modifies": len(ids), "modifications": vals})


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


def main() -> None:
    """Point d'entrée du binaire `odoo-mcp` (transport stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
