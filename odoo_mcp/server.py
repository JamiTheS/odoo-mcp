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

import json
import os

from mcp.server.fastmcp import FastMCP

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
def odoo_aggregate(model: str, groupby: str, domain: str = "[]", measure: str = "") -> str:
    """Agréger : nombre (et somme d'un champ `measure`) par valeur de `groupby`.

    Remplace read_group, qui n'est plus exposé en RPC depuis Odoo 18.
    Ex. : groupby='partner_id', measure='amount_total' sur sale.order.
    """
    c = _get_client()
    return _j(c.aggregate(model, _parse(domain, []), groupby, measure or None))


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
def odoo_unlink(model: str, ids: str) -> str:
    """Supprimer définitivement des enregistrements. IRRÉVERSIBLE.

    Bloqué tant que odoo_enable_write n'a pas été appelé. Compter et montrer
    ce qui va être supprimé avant d'appeler cet outil.
    """
    c = _get_client()
    _require_write(c)
    ok = c.unlink(model, _parse(ids, []))
    return _j({"model": model, "ids": _parse(ids, []), "deleted": ok})


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


def main() -> None:
    """Point d'entrée du binaire `odoo-mcp` (transport stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
