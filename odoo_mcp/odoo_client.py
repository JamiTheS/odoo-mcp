"""Client Odoo XML-RPC — connexion par variables d'environnement, aucun stockage.

Les identifiants (URL, login, clé API) sont fournis par l'utilisateur au début de la
conversation et passés à chaque commande en variables d'environnement inline :

    ODOO_URL=https://acme.odoo.com ODOO_USERNAME=a@b.c ODOO_API_KEY=xxx \\
        python odoo_query.py search res.partner

Rien n'est jamais écrit sur disque : ce skill est fait pour être partagé, chaque
utilisateur entre les identifiants de SA base au lancement.
"""

from __future__ import annotations

import os
import sys
import xmlrpc.client
from typing import Any

# Méthodes refusées tant que ODOO_ALLOW_WRITE n'est pas activé.
WRITE_METHODS = {
    "create", "write", "unlink", "copy", "load", "action_confirm", "button_confirm",
    "action_post", "button_validate", "action_cancel", "action_done", "toggle_active",
}


class OdooError(RuntimeError):
    pass


class ReadOnlyError(OdooError):
    pass


def mask(secret: str) -> str:
    """N'affiche jamais une clé en clair dans un log ou une sortie."""
    if not secret:
        return "(vide)"
    return f"{'*' * 8}{secret[-4:]}" if len(secret) > 4 else "****"


class OdooClient:
    def __init__(self, url: str, db: str, username: str, api_key: str,
                 readonly: bool = True, timeout: int = 60):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.api_key = api_key
        self.readonly = readonly
        self.timeout = timeout
        self.journal = None          # objet Journal, posé par le serveur si activé
        self.motif_courant = ""      # justification appliquée aux écritures suivantes
        self._uid: int | None = None
        self._models: xmlrpc.client.ServerProxy | None = None

    # -- construction
    @classmethod
    def from_env(cls) -> "OdooClient":
        url = os.environ.get("ODOO_URL", "").strip()
        username = os.environ.get("ODOO_USERNAME", "").strip()
        api_key = os.environ.get("ODOO_API_KEY", "").strip()
        missing = [n for n, v in [("ODOO_URL", url), ("ODOO_USERNAME", username),
                                  ("ODOO_API_KEY", api_key)] if not v]
        if missing:
            raise OdooError(
                "Identifiants manquants : " + ", ".join(missing) + ".\n"
                "Demande à l'utilisateur l'URL de sa base, son login et sa clé API, puis "
                "passe-les en variables d'environnement inline sur chaque commande :\n"
                "  ODOO_URL=https://acme.odoo.com ODOO_USERNAME=a@b.c ODOO_API_KEY=xxx "
                "python odoo_query.py ...\n"
                "Ne les écris dans aucun fichier."
            )
        db = os.environ.get("ODOO_DB", "").strip()
        if not db:
            # Odoo Online : la base porte le nom du sous-domaine.
            db = url.split("//")[-1].split("/")[0].split(".")[0]
        allow_write = os.environ.get("ODOO_ALLOW_WRITE", "").strip().lower() in ("1", "true", "yes")
        timeout = int(os.environ.get("ODOO_TIMEOUT", "60"))
        return cls(url=url, db=db, username=username, api_key=api_key,
                   readonly=not allow_write, timeout=timeout)

    # -- transport
    def _proxy(self, endpoint: str) -> xmlrpc.client.ServerProxy:
        cls = _TimeoutSafeTransport if self.url.startswith("https") else _TimeoutTransport
        return xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/{endpoint}", transport=cls(self.timeout), allow_none=True
        )

    def version(self) -> dict[str, Any]:
        try:
            return self._proxy("common").version()
        except Exception as exc:
            raise OdooError(f"{self.url} injoignable : {exc}") from exc

    @property
    def uid(self) -> int:
        if self._uid is None:
            self.authenticate()
        assert self._uid is not None
        return self._uid

    def authenticate(self) -> int:
        common = self._proxy("common")
        try:
            uid = common.authenticate(self.db, self.username, self.api_key, {})
        except xmlrpc.client.Fault as exc:
            raise OdooError(f"Erreur d'authentification : {exc.faultString}") from exc
        except Exception as exc:
            raise OdooError(f"{self.url} injoignable : {exc}") from exc
        if not uid:
            raise OdooError(
                f"Authentification refusée sur la base '{self.db}'.\n"
                "Vérifie : le login, la clé API, et le nom de la base (sous-domaine pour "
                "Odoo Online ; sinon passer ODOO_DB=...).\n"
                "Une clé de scope 'MCP' ne fonctionne PAS en XML-RPC : il faut une clé "
                "générée SANS scope (accès complet)."
            )
        self._uid = uid
        self._models = self._proxy("object")
        return uid

    # -- appel générique
    def execute_kw(self, model: str, method: str, args: list | None = None,
                   kwargs: dict | None = None) -> Any:
        if self.readonly and method in WRITE_METHODS:
            raise ReadOnlyError(
                f"Mode lecture seule : '{model}.{method}' est bloqué. "
                "L'écriture s'active via l'outil odoo_enable_write (ou ODOO_ALLOW_WRITE=1) — "
                "uniquement après confirmation explicite de l'utilisateur."
            )
        uid = self.uid
        assert self._models is not None

        # Capture de l'état AVANT : c'est ce qui permet de dire « X est passé de A à B »
        # dans le rapport, et pas seulement « X a été modifié ».
        avant = self._capturer_avant(model, method, args) if self.journal else None

        try:
            resultat = self._models.execute_kw(
                self.db, uid, self.api_key, model, method, args or [], kwargs or {}
            )
        except xmlrpc.client.Fault as exc:
            raise OdooError(f"{model}.{method} : {_short_fault(exc.faultString)}") from exc

        if self.journal and method in WRITE_METHODS:
            self._journaliser(model, method, args, resultat, avant)
        return resultat

    # -- journalisation
    def _capturer_avant(self, model: str, method: str, args: list | None):
        if method not in ("write", "unlink", "toggle_active") or not args:
            return None
        ids = args[0] if isinstance(args[0], list) else [args[0]]
        if not ids or not all(isinstance(i, int) for i in ids):
            return None
        champs = ["display_name"]
        if method == "write" and len(args) > 1 and isinstance(args[1], dict):
            champs += [k for k in args[1] if not k.startswith("_")]
        try:
            return self.execute_kw(model, "read", [ids[:20], champs])
        except OdooError:
            return None   # un champ illisible ne doit jamais bloquer l'écriture

    def _journaliser(self, model, method, args, resultat, avant) -> None:
        ids, nb, apres = [], 1, None
        if method == "create":
            ids = resultat if isinstance(resultat, list) else [resultat]
            nb = len(ids)
            apres = args[0] if args else None
        elif method in ("write", "toggle_active"):
            ids = args[0] if args and isinstance(args[0], list) else []
            nb = len(ids)
            apres = args[1] if args and len(args) > 1 else None
        elif method == "unlink":
            ids = args[0] if args and isinstance(args[0], list) else []
            nb = len(ids)
        elif method == "load":
            ids = (resultat or {}).get("ids") or []
            nb = len(ids)
        else:
            ids = args[0] if args and isinstance(args[0], list) else []
            nb = len(ids) or 1
        self.journal.enregistrer(model, method, nb, ids=ids, avant=avant,
                                 apres=apres, motif=self.motif_courant)

    # -- raccourcis de lecture
    def search_read(self, model, domain=None, fields=None, limit=None, order=None, offset=0):
        kw: dict[str, Any] = {"offset": offset}
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        return self.execute_kw(model, "search_read", [domain or [], fields or []], kw)

    def search_count(self, model, domain=None):
        return self.execute_kw(model, "search_count", [domain or []])

    def read(self, model, ids, fields=None):
        return self.execute_kw(model, "read", [ids, fields or []])

    def fields_get(self, model, attributes=None):
        return self.execute_kw(
            model, "fields_get", [[]],
            {"attributes": attributes or ["string", "type", "relation", "required",
                                          "readonly", "store", "selection"]},
        )

    def aggregate(self, model, domain, groupby: str, measures: list[str] | None = None,
                  limit: int = 20000):
        """Agrégation côté client : compte et somme(s) par valeur de `groupby`.

        `read_group` n'est plus exposé en RPC depuis Odoo 18/19 : on agrège ici.

        `groupby` accepte une granularité sur les champs date : 'date_order:month'
        (aussi day, week, quarter, year) — indispensable pour un CA par mois.
        """
        measures = [m for m in (measures or []) if m]
        field, _, granularity = groupby.partition(":")
        rows = self.execute_kw(model, "search_read",
                               [domain or [], [field] + measures], {"limit": limit})
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            key = _group_key(r.get(field), granularity)
            slot = out.setdefault(key, {"count": 0})
            slot["count"] += 1
            for m in measures:
                slot[m] = slot.get(m, 0.0) + (r.get(m) or 0)
        return dict(sorted(out.items()))

    # -- écriture
    def create(self, model, values: dict | list[dict]):
        return self.execute_kw(model, "create", [values])

    def write(self, model, ids: list[int], values: dict):
        return self.execute_kw(model, "write", [ids, values])

    def unlink(self, model, ids: list[int]):
        return self.execute_kw(model, "unlink", [ids])

    def load(self, model, fields: list[str], rows: list[list[str]]) -> dict:
        """Import natif Odoo. Gère les External ID et les relations `champ/id`.

        Rejette le lot entier en cas d'erreur : rien n'est écrit partiellement.
        """
        res = self.execute_kw(model, "load", [fields, rows])
        errors = [m for m in res.get("messages", []) if m.get("type") == "error"]
        if errors:
            raise OdooError("load() a échoué :\n" + _format_load_errors(errors))
        return res

    # -- External ID
    def ref(self, xmlid: str) -> int | None:
        module, _, name = xmlid.partition(".")
        if not name:
            module, name = "__import__", xmlid
        r = self.execute_kw("ir.model.data", "search_read",
                            [[["module", "=", module], ["name", "=", name]], ["res_id"]])
        return r[0]["res_id"] if r else None

    def set_ref(self, xmlid: str, model: str, res_id: int) -> None:
        module, _, name = xmlid.partition(".")
        if not name:
            module, name = "__import__", xmlid
        if not self.execute_kw("ir.model.data", "search_count",
                               [[["module", "=", module], ["name", "=", name]]]):
            self.execute_kw("ir.model.data", "create",
                            [{"module": module, "name": name,
                              "model": model, "res_id": res_id}])

    def ensure(self, xmlid: str, model: str, values: dict, update: bool = True) -> int:
        """Crée l'enregistrement, ou le met à jour s'il existe déjà sous cet External ID.

        C'est ce qui rend un script rejouable sans créer de doublon.
        """
        existing = self.ref(xmlid)
        if existing:
            if update:
                self.write(model, [existing], values)
            return existing
        new_id = self.create(model, values)
        self.set_ref(xmlid, model, new_id)
        return new_id


# ------------------------------------------------------------------ utilitaires
def _group_key(value, granularity: str = "") -> str:
    """Clé de regroupement lisible : many2one -> libellé, date -> période demandée."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[1])
    if value is False or value is None:
        return "(vide)"
    text = str(value)
    if not granularity:
        return text
    # Les dates Odoo arrivent en 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM:SS'.
    date_part = text[:10]
    try:
        y, m, d = (int(x) for x in date_part.split("-"))
    except ValueError:
        return text
    if granularity == "year":
        return f"{y}"
    if granularity == "quarter":
        return f"{y}-T{(m - 1) // 3 + 1}"
    if granularity == "month":
        return f"{y}-{m:02d}"
    if granularity == "week":
        import datetime
        iso = datetime.date(y, m, d).isocalendar()
        return f"{iso[0]}-S{iso[1]:02d}"
    return date_part


def _short_fault(text: str) -> str:
    """Les traces Odoo font 50 lignes ; on garde la cause utile."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    for ln in reversed(lines):
        s = ln.strip()
        if s.startswith(("File ", "Traceback", "raise ", "  ")):
            continue
        return s[:400]
    return text[:400]


def _format_load_errors(errors: list[dict]) -> str:
    out = []
    for m in errors[:8]:
        msg = (m.get("message") or "").strip()
        if not msg:
            # load() renvoie parfois un message vide : le reste du dict porte l'info
            # (souvent une validation de champ, ex. un numéro de TVA invalide).
            msg = f"(sans message) champ={m.get('field')} valeur={m.get('value')!r}"
        out.append(f"  ligne {m.get('record')} : {msg}")
    if len(errors) > 8:
        out.append(f"  ... et {len(errors) - 8} autres")
    return "\n".join(out)


class _TimeoutMixin:
    def __init__(self, timeout: int) -> None:
        super().__init__(use_datetime=True)  # type: ignore[misc]
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)  # type: ignore[misc]
        conn.timeout = self._timeout
        return conn


class _TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    pass


class _TimeoutSafeTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    pass


if __name__ == "__main__":
    sys.exit("Ce fichier est une bibliothèque. Utilise odoo_connect.py / odoo_query.py.")
