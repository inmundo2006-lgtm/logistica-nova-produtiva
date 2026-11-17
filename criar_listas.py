# -*- coding: utf-8 -*-
r"""
Cria no SharePoint as 3 listas do app (RotasFuncionarios, RotasFrotas, RotasConfig) com as colunas certas.

  python criar_listas.py <NomeDoSite>             -> só mostra o que faria
  python criar_listas.py <NomeDoSite> --criar     -> cria

<NomeDoSite> é o trecho da URL depois de /sites/ (ex.: NovaProdutiva).
Usa a credencial de um secrets.toml com permissão Sites.FullControl.All (variável ROTAS_CRED, ou o do app).
Só cria o que falta: lista ou coluna que já existe é mantida como está. Não apaga nem altera nada.
"""
import os, sys, tomllib
import requests

PASTA = os.path.dirname(os.path.abspath(__file__))
CRED = os.environ.get("ROTAS_CRED") or os.path.join(PASTA, ".streamlit", "secrets.toml")
GRAPH = "https://graph.microsoft.com/v1.0"
TXT = lambda nome: {"name": nome, "text": {}}
LISTAS = {
    "RotasFuncionarios": [TXT(c) for c in ("Nome", "Funcao", "Setor", "CentroCusto", "Turno", "Localidade", "Transporte", "Observacao")],
    "RotasFrotas": [TXT("Tipo"), {"name": "Lugares", "number": {"decimalPlaces": "none"}},
                    TXT("Prestador"), TXT("Frota"), TXT("Situacao"), TXT("Observacao")],
    "RotasConfig": [{"name": "Valor", "text": {"allowMultipleLines": True, "textType": "plain", "linesForEditing": 6}}],
}


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    site, criar = sys.argv[1], "--criar" in sys.argv
    with open(CRED, "rb") as fp:
        s = tomllib.load(fp)
    r = requests.post(f"https://login.microsoftonline.com/{s['TENANT_ID']}/oauth2/v2.0/token", timeout=30,
                      data={"grant_type": "client_credentials", "client_id": s["CLIENT_ID"],
                            "client_secret": s["CLIENT_SECRET"], "scope": "https://graph.microsoft.com/.default"})
    r.raise_for_status()
    H = {"Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/json"}
    host = s["SITE_HOST"]
    r = requests.get(f"{GRAPH}/sites/{host}:/sites/{site}", headers=H, timeout=30)
    if r.status_code == 404:
        raise SystemExit(f"Site não encontrado: https://{host}/sites/{site}")
    r.raise_for_status()
    sid = r.json()["id"]
    print("site:", r.json().get("displayName"), "|", r.json().get("webUrl"))
    existentes = {l["displayName"]: l["id"] for l in requests.get(f"{GRAPH}/sites/{sid}/lists?$top=200", headers=H, timeout=30).json()["value"]}
    for nome, colunas in LISTAS.items():
        if nome in existentes:
            lid = existentes[nome]
            tem = {c["name"] for c in requests.get(f"{GRAPH}/sites/{sid}/lists/{lid}/columns?$top=200", headers=H, timeout=30).json()["value"]}
            faltam = [c for c in colunas if c["name"] not in tem]
            print(f"{nome}: já existe; colunas que faltam: {[c['name'] for c in faltam] or 'nenhuma'}")
            if criar:
                for c in faltam:
                    requests.post(f"{GRAPH}/sites/{sid}/lists/{lid}/columns", headers=H, json=c, timeout=30).raise_for_status()
                    print("   + coluna", c["name"])
        else:
            print(f"{nome}: não existe -> criar com {[c['name'] for c in colunas]}")
            if criar:
                r = requests.post(f"{GRAPH}/sites/{sid}/lists", headers=H, timeout=60,
                                  json={"displayName": nome, "columns": colunas, "list": {"template": "genericList"}})
                if r.status_code == 403:
                    raise SystemExit("403: a credencial não tem permissão para criar listas neste site.")
                r.raise_for_status()
                print("   criada:", r.json().get("webUrl"))
    if not criar:
        print("Nada criado. Rode de novo com --criar.")


if __name__ == "__main__":
    main()
