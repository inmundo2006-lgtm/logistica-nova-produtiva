# -*- coding: utf-8 -*-
r"""
Carga inicial dos colaboradores a partir de uma planilha (Cadastro, Nome, Função, Setor, CC, Escala, Turno, Cidade...).

  python importar_inicial.py <planilha.xlsx>             -> só mostra o que faria
  python importar_inicial.py <planilha.xlsx> --gravar    -> grava no banco configurado nos secrets

O banco é o mesmo do app: .streamlit\secrets.toml com MODO_LOCAL = true grava em dados_locais\; com as
credenciais do SharePoint grava na lista RotasFuncionarios. Não duplica: pula cadastros que já existem.
Quem tem carro alugado entra como "Carro alugado" (fora das rotas); os demais como "Fretado".
"""
import os, sys, tomllib
import pandas as pd
import banco

PASTA = os.path.dirname(os.path.abspath(__file__))


def abrir_banco():
    with open(os.path.join(PASTA, ".streamlit", "secrets.toml"), "rb") as fp:
        s = tomllib.load(fp)
    lista = s.get("LISTA_FUNCIONARIOS", "RotasFuncionarios")
    if s.get("MODO_LOCAL"):
        return banco.Local(), lista
    return banco.SharePoint(s["TENANT_ID"], s["CLIENT_ID"], s["CLIENT_SECRET"],
                            s["SITE_HOST"], s["SITE_NAME"]), lista


def main():
    planilha = sys.argv[1]; gravar = "--gravar" in sys.argv
    df = pd.read_excel(planilha)
    df.columns = ["Qtde", "Cadastro", "Nome", "Funcao", "Setor", "CC", "Escala", "Turno", "Cidade", "HR", "Veiculo", "Prest"][:len(df.columns)]
    bd, lista = abrir_banco()
    ja = {str(i.get("Title")) for i in bd.listar(lista)}
    novos = []
    for _, r in df.iterrows():
        cad = str(int(r.Cadastro))
        if cad in ja:
            continue
        alugado = ("Veiculo" in df.columns) and r.Veiculo == "CARRO" and pd.isna(r.HR)
        novos.append(dict(Title=cad, Nome=str(r.Nome).strip().upper(), Funcao=str(r.Funcao).strip(), Setor=str(r.Setor).strip(),
                          CentroCusto=str(r.CC).strip(), Turno=str(r.Turno).strip(), Localidade=str(r.Cidade).strip().upper(),
                          Transporte="Carro alugado" if alugado else "Fretado", Observacao=""))
    print(f"banco: {bd.nome} | lista: {lista} | já existem: {len(ja)} | a incluir: {len(novos)} "
          f"({sum(1 for n in novos if n['Transporte'] == 'Fretado')} no fretado)")
    if not gravar:
        print("Nada gravado. Rode de novo com --gravar para incluir."); return
    for k, n in enumerate(novos, 1):
        bd.criar(lista, n)
        if k % 50 == 0: print(f"  {k}/{len(novos)}", flush=True)
    print("pronto:", len(novos), "colaboradores incluídos")


if __name__ == "__main__":
    main()
