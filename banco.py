# -*- coding: utf-8 -*-
"""
Banco de dados do app: listas do SharePoint (Microsoft Graph) ou, em modo local, arquivos JSON.

Listas (nomes configuráveis nos secrets):
  RotasFuncionarios  Title = cadastro | Nome, Funcao, Setor, CentroCusto, Turno, Localidade, Transporte, Observacao
  RotasFrotas        Title = identificação (placa/nº) | Tipo, Lugares, Prestador, Frota, Situacao, Observacao
  RotasConfig        Title = chave | Valor (várias linhas de texto) - parâmetros e o último resultado calculado

Modo local (secrets com MODO_LOCAL = true): grava em dados_locais/*.json. Serve para testar no PC sem tocar
no SharePoint. A pasta dados_locais fica fora do Git (tem nomes de colaboradores).
"""
import json, os, threading

import requests

PASTA = os.path.dirname(os.path.abspath(__file__))
GRAPH = "https://graph.microsoft.com/v1.0"
PEDACO = 55000      # o campo de várias linhas do SharePoint aceita ~63 mil caracteres


class Local:
    """Mesmo contrato do SharePoint, em arquivos JSON."""
    nome = "local"

    def __init__(self, pasta=None):
        self.pasta = pasta or os.path.join(PASTA, "dados_locais")
        os.makedirs(self.pasta, exist_ok=True)
        self._trava = threading.Lock()

    def _arq(self, lista):
        return os.path.join(self.pasta, f"{lista}.json")

    def _ler(self, lista):
        try:
            with open(self._arq(lista), encoding="utf-8") as fp:
                return json.load(fp)
        except FileNotFoundError:
            return {"prox": 1, "itens": []}

    def _gravar(self, lista, d):
        tmp = self._arq(lista) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=1)
        os.replace(tmp, self._arq(lista))

    def listar(self, lista):
        return [dict(i) for i in self._ler(lista)["itens"]]

    def criar(self, lista, campos):
        with self._trava:
            d = self._ler(lista)
            item = dict(campos, id=str(d["prox"]))
            d["prox"] += 1; d["itens"].append(item); self._gravar(lista, d)
            return item

    def atualizar(self, lista, item_id, campos):
        with self._trava:
            d = self._ler(lista)
            for i in d["itens"]:
                if i["id"] == str(item_id):
                    i.update(campos)
            self._gravar(lista, d)

    def apagar(self, lista, item_id):
        with self._trava:
            d = self._ler(lista)
            d["itens"] = [i for i in d["itens"] if i["id"] != str(item_id)]
            self._gravar(lista, d)


class SharePoint:
    nome = "sharepoint"

    def __init__(self, tenant, client_id, client_secret, host, site):
        self.tenant, self.client_id, self.client_secret = tenant, client_id, client_secret
        self.host, self.site = host, site
        self._token, self._site_id = None, None

    def _hdrs(self):
        if not self._token:
            r = requests.post(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", timeout=30,
                              data={"grant_type": "client_credentials", "client_id": self.client_id,
                                    "client_secret": self.client_secret, "scope": "https://graph.microsoft.com/.default"})
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _pedir(self, metodo, url, **kw):
        r = requests.request(metodo, url, headers=self._hdrs(), timeout=60, **kw)
        if r.status_code == 401:            # token venceu: pega outro e tenta de novo
            self._token = None
            r = requests.request(metodo, url, headers=self._hdrs(), timeout=60, **kw)
        r.raise_for_status()
        return r

    def _base(self, lista):
        if not self._site_id:
            self._site_id = self._pedir("GET", f"{GRAPH}/sites/{self.host}:/sites/{self.site}").json()["id"]
        return f"{GRAPH}/sites/{self._site_id}/lists/{lista}/items"

    def listar(self, lista):
        url, out = self._base(lista) + "?expand=fields&$top=2000", []
        while url:                           # a lista vem em páginas
            j = self._pedir("GET", url).json()
            for it in j.get("value", []):
                out.append(dict(it.get("fields", {}), id=str(it["id"])))
            url = j.get("@odata.nextLink")
        return out

    def criar(self, lista, campos):
        j = self._pedir("POST", self._base(lista), json={"fields": campos}).json()
        return dict(j.get("fields", {}), id=str(j["id"]))

    def atualizar(self, lista, item_id, campos):
        self._pedir("PATCH", f"{self._base(lista)}/{item_id}/fields", json=campos)

    def apagar(self, lista, item_id):
        self._pedir("DELETE", f"{self._base(lista)}/{item_id}")


# ----------------------------------------------------------------------------- chave/valor (parâmetros e resultado)
def ler_config(bd, lista, chave):
    """Valor JSON guardado na lista de configuração (quebrado em pedaços chave#0, chave#1...). None se não há."""
    partes = {}
    for it in bd.listar(lista):
        t = str(it.get("Title") or "")
        if t.startswith(chave + "#"):
            partes[int(t.split("#")[1])] = it.get("Valor") or ""
    if not partes:
        return None
    try:
        return json.loads("".join(partes[k] for k in sorted(partes)))
    except (ValueError, KeyError):
        return None


def gravar_config(bd, lista, chave, valor):
    txt = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    pedacos = [txt[i:i + PEDACO] for i in range(0, len(txt), PEDACO)] or [""]
    atuais = {str(it.get("Title")): it["id"] for it in bd.listar(lista) if str(it.get("Title") or "").startswith(chave + "#")}
    for k, p in enumerate(pedacos):
        t = f"{chave}#{k}"
        if t in atuais:
            bd.atualizar(lista, atuais.pop(t), {"Valor": p})
        else:
            bd.criar(lista, {"Title": t, "Valor": p})
    for sobra in atuais.values():            # o valor novo ficou menor: apaga os pedaços que sobraram
        bd.apagar(lista, sobra)
