# -*- coding: utf-8 -*-
"""
Logística de fretamento - Usina Nova Produtiva (Streamlit).

Cadastro de colaboradores, frotas e turnos (listas do SharePoint) + cálculo das rotas (motor.py) + mapa (mapa.py).
Rodar no PC:  streamlit run app.py
"""
import base64, copy, hashlib, io, json, os, re
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import banco, mapa, motor

st.set_page_config(page_title="Logística Nova Produtiva", page_icon="🚌", layout="wide",
                   initial_sidebar_state="collapsed")

PASTA = os.path.dirname(os.path.abspath(__file__))
FUSO_BRASILIA = timezone(timedelta(hours=-3))      # o servidor do Streamlit Cloud roda em UTC

# Identidade visual: a mesma paleta do Controle de Frotas (tema claro, dourado). Também no .streamlit/config.toml.
COR_DOURADO, COR_DOURADO_CLARO = "#9B8328", "#EFE6C4"
COR_SUPERFICIE, COR_TEXTO, COR_TEXTO_FRACO, COR_BORDA = "#FFFFFF", "#1F1C14", "#6B6556", "#DED7BA"
COR_IDA, COR_VOLTA = "#2a78d6", "#eb6834"

TRANSPORTES = ["Fretado", "Carro alugado", "Veículo próprio", "Outro"]
GRUPOS = {"IND": "Industrial", "AGR": "Agrícola"}
SITUACOES = ["Ativo", "Reserva", "Inativo"]
PARADAS_PADRAO = {"ASTORGA": 15, "FLORESTOPOLIS": 10, "JAGUAPITÃ": 6, "MUNHOZ DE MELO": 6, "PORECATU": 5,
                  "SANTA ZELIA": 6, "IÇARA": 5, "TUPINAMBA": 4, "ARAPONGAS": 5}


def _agora():
    return datetime.now(FUSO_BRASILIA)


def _logo_html(altura=40):
    try:
        with open(os.path.join(PASTA, "assets", "logo.png"), "rb") as fp:
            b64 = base64.b64encode(fp.read()).decode()
    except OSError:
        return ""
    return f"<img src='data:image/png;base64,{b64}' height='{altura}' alt='Nova Produtiva'/>"


def _kpi(col, valor, rotulo, sub=""):
    col.markdown(
        f'<div style="background:{COR_SUPERFICIE};border:1px solid {COR_BORDA};border-left:5px solid {COR_DOURADO};'
        f'border-radius:8px;padding:8px 14px;margin-bottom:8px">'
        f'<div style="font-size:11.5px;color:{COR_TEXTO_FRACO}">{rotulo}</div>'
        f'<div style="font-size:26px;font-weight:700;color:{COR_TEXTO};line-height:1.2">{valor}</div>'
        f'<div style="font-size:11.5px;color:{COR_TEXTO_FRACO}">{sub}&nbsp;</div></div>', unsafe_allow_html=True)


def _nf(n, casas=0):
    return f"{n:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ----------------------------------------------------------------------------- LOGIN
# Usuários nos secrets:   [usuarios.joao]  senha_sha256 = "..."  perfil = "admin" | "visualizador"
#   admin        -> cadastra, altera, exclui e recalcula a logística
#   visualizador -> só consulta
MODO_LOCAL = bool(st.secrets.get("MODO_LOCAL", False))
USUARIOS = st.secrets.get("usuarios", {})

if "auth_usuario" not in st.session_state:
    _livre = (MODO_LOCAL or bool(st.secrets.get("LOGIN_LIVRE", False))) and not USUARIOS   # só no PC
    st.session_state["auth_usuario"] = "local" if _livre else None
    st.session_state["auth_perfil"] = "admin" if _livre else None

if not st.session_state["auth_usuario"]:
    _c1, _c2, _c3 = st.columns([1, 1, 1])
    with _c2:
        st.markdown(f"<div style='text-align:center;margin:40px 0 6px'>{_logo_html(110)}</div>"
                    f"<div style='text-align:center;color:{COR_TEXTO_FRACO};font-size:14px;margin-bottom:14px'>"
                    f"Logística de Fretamento · Acesso ao Sistema</div>", unsafe_allow_html=True)
        with st.form("form_login"):
            _u = st.text_input("Usuário")
            _s = st.text_input("Senha", type="password")
            _ok = st.form_submit_button("Entrar", type="primary", width="stretch")
        if _ok:
            u = USUARIOS.get(_u.strip())
            if not USUARIOS:
                st.error("Nenhum usuário configurado em st.secrets['usuarios']. Avise o administrador.")
            elif u and hashlib.sha256(_s.encode("utf-8")).hexdigest() == u.get("senha_sha256", ""):
                st.session_state["auth_usuario"] = _u.strip()
                st.session_state["auth_perfil"] = u.get("perfil", "visualizador")
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")
    st.stop()

USUARIO = st.session_state["auth_usuario"]
PODE_EDITAR = st.session_state["auth_perfil"] == "admin"

# ----------------------------------------------------------------------------- BANCO
LISTA_FUNC = st.secrets.get("LISTA_FUNCIONARIOS", "RotasFuncionarios")
LISTA_FROTAS = st.secrets.get("LISTA_FROTAS", "RotasFrotas")
LISTA_CONFIG = st.secrets.get("LISTA_CONFIG", "RotasConfig")


@st.cache_resource
def _bd():
    if MODO_LOCAL:
        return banco.Local()
    return banco.SharePoint(st.secrets["TENANT_ID"], st.secrets["CLIENT_ID"], st.secrets["CLIENT_SECRET"],
                            st.secrets["SITE_HOST"], st.secrets["SITE_NAME"])


@st.cache_data
def _fixos():
    with open(os.path.join(PASTA, "dados", "matriz.json"), encoding="utf-8") as fp:
        M = json.load(fp)
    with open(os.path.join(PASTA, "dados", "geometrias.json"), encoding="utf-8") as fp:
        GEO = json.load(fp)
    return M, GEO


M, GEO = _fixos()
LOCALIDADES = [n for n in M["nomes"] if n != "USINA"]


@st.cache_data(ttl=60, show_spinner="Lendo colaboradores...")
def carregar_funcs():
    out = []
    for it in _bd().listar(LISTA_FUNC):
        out.append(dict(id=it["id"], Cadastro=str(it.get("Title") or "").strip(), Nome=it.get("Nome") or "",
                        Funcao=it.get("Funcao") or "", Setor=it.get("Setor") or "", CentroCusto=it.get("CentroCusto") or "",
                        Turno=it.get("Turno") or "", Localidade=it.get("Localidade") or "",
                        Transporte=it.get("Transporte") or "", Observacao=it.get("Observacao") or ""))
    out.sort(key=lambda f: f["Nome"])
    return out


@st.cache_data(ttl=60, show_spinner="Lendo frotas...")
def carregar_frotas():
    out = []
    for it in _bd().listar(LISTA_FROTAS):
        try:
            lug = int(float(it.get("Lugares") or 0))
        except (TypeError, ValueError):
            lug = 0
        out.append(dict(id=it["id"], Identificacao=str(it.get("Title") or "").strip(), Tipo=it.get("Tipo") or "",
                        Lugares=lug, Prestador=it.get("Prestador") or "", Frota=it.get("Frota") or "",
                        Situacao=it.get("Situacao") or "Ativo", Observacao=it.get("Observacao") or ""))
    out.sort(key=lambda f: (f["Frota"], f["Tipo"], f["Identificacao"]))
    return out


@st.cache_data(ttl=60, show_spinner="Lendo parâmetros e resultado...")
def carregar_config():
    bd = _bd()
    return banco.ler_config(bd, LISTA_CONFIG, "parametros"), banco.ler_config(bd, LISTA_CONFIG, "resultado")


def invalidar():
    carregar_funcs.clear(); carregar_frotas.clear(); carregar_config.clear()


def parametros(salvos):
    cfg = copy.deepcopy(motor.PADRAO)
    cfg["paradas"] = dict(PARADAS_PADRAO)
    for k, v in (salvos or {}).items():
        if k in cfg:
            cfg[k] = v
    return cfg


try:
    FUNCS = carregar_funcs()
    FROTAS = carregar_frotas()
    _salvos, RES = carregar_config()
except Exception as e:   # noqa: BLE001 - qualquer falha de rede/lista vira aviso na tela
    st.error(f"Não consegui ler o banco de dados ({'arquivos locais' if MODO_LOCAL else 'SharePoint'}): {e}")
    st.info("Confira nos Secrets o site (SITE_NAME) e se as listas RotasFuncionarios, RotasFrotas e RotasConfig existem.")
    st.stop()
CFG = parametros(_salvos)
TURNOS = [t["nome"] for t in CFG["turnos"]]
TIPOS_NOME = {t: v["nome"] for t, v in CFG["veiculos"].items()}
ASSINATURA = motor.assinatura(FUNCS, CFG)
DESATUALIZADO = bool(RES) and RES.get("assinatura") != ASSINATURA

# ----------------------------------------------------------------------------- CABEÇALHO
st.markdown("""
<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding: 1rem 1.5rem !important;}
.stTabs [data-baseweb="tab-list"] {gap: 8px;}
.stTabs [data-baseweb="tab"] {padding: 6px 16px; border-radius: 8px 8px 0 0;}
.stTabs [data-baseweb="tab"]:hover {color: #9B8328;}
.stTabs [aria-selected="true"] {font-weight: 600;}
</style>""", unsafe_allow_html=True)
st.markdown(
    f"<div style='display:flex;align-items:center;gap:14px;padding:2px 0 10px;border-bottom:2px solid {COR_DOURADO};"
    f"margin-bottom:6px'>{_logo_html(54)}<div style='border-left:1px solid {COR_BORDA};padding-left:14px'>"
    f"<div style='font-size:20px;font-weight:700;color:{COR_TEXTO};line-height:1.2'>Logística de Fretamento</div>"
    f"<div style='font-size:12px;color:{COR_TEXTO_FRACO}'>Usina Nova Produtiva · Astorga-PR</div></div></div>",
    unsafe_allow_html=True)
_h1, _h2 = st.columns([5, 1])
with _h1:
    st.caption(f"Usuário: **{USUARIO}** · Perfil: {'🔑 admin' if PODE_EDITAR else '👁️ visualizador'}"
               + (" · 💾 modo local (teste, não grava no SharePoint)" if MODO_LOCAL else ""))
with _h2:
    if st.button("Sair", width="stretch"):
        st.session_state["auth_usuario"] = None; st.session_state["auth_perfil"] = None
        st.rerun()


# ----------------------------------------------------------------------------- CÁLCULO
def recalcular():
    with st.status("Calculando a logística (leva alguns minutos, não feche a página)...", expanded=True) as stt:
        try:
            res = motor.calcular(FUNCS, M, CFG, avisar=st.write)
        except motor.ErroDados as e:
            stt.update(label="Não deu para calcular", state="error")
            st.error(str(e))
            return
        res["quando"] = _agora().strftime("%d/%m/%Y %H:%M"); res["quem"] = USUARIO
        st.write("Gravando o resultado...")
        banco.gravar_config(_bd(), LISTA_CONFIG, "resultado", res)
        stt.update(label="Logística calculada", state="complete")
    invalidar()
    st.rerun()


def faixa_calculo(chave):
    """Aviso de resultado desatualizado + botão de recalcular."""
    n_fret = sum(1 for f in FUNCS if motor.usa_fretado(f))
    if not RES:
        st.warning(f"A logística ainda não foi calculada. Há {n_fret} colaboradores no fretado.")
    elif DESATUALIZADO:
        st.warning(f"⚠️ O cadastro mudou depois do último cálculo ({RES.get('quando', '?')}). "
                   "Os números abaixo são do cálculo anterior.")
    else:
        st.caption(f"✅ Logística em dia com o cadastro · calculada em {RES.get('quando', '?')} por {RES.get('quem', '?')}")
    if PODE_EDITAR and (not RES or DESATUALIZADO or st.session_state.get("mostrar_recalc")):
        if st.button("🔄 Recalcular a logística", type="primary", key=f"recalc_{chave}", disabled=n_fret == 0):
            recalcular()


def df_escala():
    """Colaborador por colaborador: veículo e horário de ida e de volta (do último cálculo)."""
    aloc = (RES or {}).get("alocacao", {})
    linhas = []
    for f in FUNCS:
        a = aloc.get(f["Cadastro"], {})
        ida, volta = a.get("IDA", {}), a.get("VOLTA", {})
        linhas.append({"Cadastro": f["Cadastro"], "Nome": f["Nome"], "Função": f["Funcao"], "Setor": f["Setor"],
                       "Turno": f["Turno"], "Localidade": mapa.rotulo(f["Localidade"]), "Transporte": f["Transporte"],
                       "Veículo ida": ida.get("veiculo", ""), "Embarque": ida.get("hora", ""),
                       "Chega usina": ida.get("usina", ""), "Veículo volta": volta.get("veiculo", ""),
                       "Sai usina": volta.get("usina", ""), "Desembarque": volta.get("hora", "")})
    return pd.DataFrame(linhas)


def excel_de(abas):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for nome, df in abas.items():
            df.to_excel(xw, sheet_name=nome[:31], index=False)
            ws = xw.sheets[nome[:31]]
            for k, col in enumerate(df.columns, 1):
                larg = max([len(str(col))] + [len(str(v)) for v in df[col].head(400)]) + 2
                ws.column_dimensions[ws.cell(1, k).column_letter].width = min(larg, 48)
            ws.freeze_panes = "A2"
    return buf.getvalue()


# ----------------------------------------------------------------------------- ABAS
abas = ["📊 Painel", "🗺️ Mapa das rotas", "🔁 Ida e volta por turno", "👥 Colaboradores", "🚌 Frotas"]
if PODE_EDITAR:
    abas.append("⚙️ Turnos e veículos")
tabs = st.tabs(abas)

# ============================================================================= PAINEL
with tabs[0]:
    faixa_calculo("painel")
    if RES:
        R = RES["resumo"]
        comp = " · ".join(f"{GRUPOS.get(g, g)[:3]}.: " + (", ".join(f"{n} {TIPOS_NOME.get(t, t).lower()}" for t, n in f.items()) or "nenhum")
                          for g, f in RES["frota"].items())
        c = st.columns(5)
        _kpi(c[0], _nf(R["veiculos"]), "Veículos no total", comp)
        _kpi(c[1], _nf(R["ocupacao"], 1) + "%", "Ocupação (pessoas ÷ lugares)", f"{_nf(R['pax'])} pessoas em {_nf(R['assentos'])} lugares, somando as viagens")
        _kpi(c[2], _nf(R["viagens"]), "Viagens por dia", f"{_nf(R['pessoas'])} colaboradores no fretado")
        _kpi(c[3], _nf(R["kmTotal"]), "km total por dia", f"{_nf(R['kmPax'])} km com passageiros")
        _kpi(c[4], _nf(R["kmVazio"]), "km vazio por dia",
             f"{_nf(100 * R['kmVazio'] / R['kmTotal'], 1)}% do km total" if R["kmTotal"] else "")

        st.markdown("#### Veículo por veículo")
        dv = pd.DataFrame([{"Veículo": v["nome"], "Frota": GRUPOS.get(v["grupo"], v["grupo"]), "Lugares": v["lugares"],
                            "Viagens/dia": v["viagens"], "Pessoas (soma das viagens)": v["pax"],
                            "Lugares oferecidos": v["assentos"], "Ocupação %": round(100 * v["pax"] / v["assentos"], 1) if v["assentos"] else 0,
                            "km com passageiros": v["km"], "km vazio": v["kmVazio"], "km total": round(v["km"] + v["kmVazio"], 1)}
                           for v in RES["veiculos"]])
        st.dataframe(dv, hide_index=True, width="stretch",
                     column_config={"Ocupação %": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)})

        st.markdown("#### Ocupação por turno: ida e volta")
        linhas = []
        for k, t in enumerate(CFG["turnos"]):
            for s, rot in (("IDA", "Ida"), ("VOLTA", "Volta")):
                vs = [v for v in RES["viagens"] if v["onda"] == f"T{k}_{s}"]
                if vs:
                    pax, lug = sum(v["ocup"] for v in vs), sum(v["cap"] for v in vs)
                    linhas.append({"Turno": t.get("descricao") or t["nome"], "Sentido": rot,
                                   "Horário": t["entrada"] if s == "IDA" else t["saida"], "Veículos": len(vs),
                                   "Pessoas": pax, "Lugares": lug, "Ocupação %": round(100 * pax / lug, 1),
                                   "km": round(sum(v["km"] for v in vs), 1)})
        st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch",
                     column_config={"Ocupação %": st.column_config.ProgressColumn(format="%.1f%%", min_value=0, max_value=100)})

        # frota que a logística pede x frota cadastrada
        st.markdown("#### Frota necessária × frota cadastrada")
        ativos = [f for f in FROTAS if f["Situacao"] == "Ativo"]
        linhas = []
        for g, f in RES["frota"].items():
            for t, n in f.items():
                tem = sum(1 for x in ativos if x["Frota"] == GRUPOS.get(g, g) and x["Tipo"] == TIPOS_NOME.get(t, t))
                linhas.append({"Frota": GRUPOS.get(g, g), "Tipo": TIPOS_NOME.get(t, t), "Necessários": n, "Cadastrados (ativos)": tem,
                               "Situação": "✅ ok" if tem >= n else f"⚠️ faltam {n - tem}"})
        st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch")
        if not FROTAS:
            st.caption("Nenhum veículo cadastrado ainda na aba 🚌 Frotas.")

        if RES.get("pendencias"):
            with st.expander(f"⚠️ {len(RES['pendencias'])} colaboradores do fretado ficaram fora do cálculo"):
                st.dataframe(pd.DataFrame(RES["pendencias"]), hide_index=True, width="stretch")
        aviso = [f"{GRUPOS.get(g, g)}: {s[e]['status']}" for g, s in RES.get("status", {}).items()
                 for e in ("E1", "E2") if e in s and s[e]["status"] != "Optimal"]
        if aviso:
            st.caption("ℹ️ O otimizador parou no limite de tempo em: " + "; ".join(aviso)
                       + ". A solução vale, mas pode existir uma um pouco melhor.")
        if PODE_EDITAR and not DESATUALIZADO:
            st.checkbox("Mostrar o botão de recalcular mesmo sem mudança no cadastro", key="mostrar_recalc")

# ============================================================================= MAPA
with tabs[1]:
    if not RES:
        st.info("Calcule a logística na aba 📊 Painel para ver o mapa.")
    else:
        if DESATUALIZADO:
            st.warning("⚠️ Mapa do cálculo anterior: o cadastro mudou depois. Recalcule na aba 📊 Painel.")
        altura = 1180 + 34 * len(RES["veiculos"]) + 30 * len(CFG["turnos"])
        _html = mapa.pagina(RES, M, GEO, CFG)
        if hasattr(st, "iframe"):          # Streamlit novo; o components.html está sendo aposentado
            st.iframe(_html, height=altura)
        else:
            components.html(_html, height=altura, scrolling=True)

# ============================================================================= IDA E VOLTA
with tabs[2]:
    if not RES:
        st.info("Calcule a logística na aba 📊 Painel para ver as rotas.")
    else:
        if DESATUALIZADO:
            st.warning("⚠️ Rotas do cálculo anterior: o cadastro mudou depois. Recalcule na aba 📊 Painel.")
        ver_nomes = st.checkbox("Mostrar os nomes dos colaboradores em cada veículo")
        esc_df = df_escala() if ver_nomes else None

        def cartao(v, sentido):
            cor = COR_IDA if sentido == "IDA" else COR_VOLTA
            pct = round(100 * v["ocup"] / v["cap"])
            paradas = " → ".join(f"<b>{h}</b> {mapa.rotulo(l)}" + (f" ({v['carga'][l]})" if l != "USINA" else "")
                                 for l, h in v["paradas"])
            st.markdown(
                f'<div style="background:{COR_SUPERFICIE};border:1px solid {COR_BORDA};border-left:5px solid {cor};'
                f'border-radius:8px;padding:8px 12px;margin-bottom:8px">'
                f'<div style="display:flex;justify-content:space-between;gap:8px"><b>{v["veiculo"]}</b>'
                f'<span style="color:{COR_TEXTO_FRACO};font-size:12.5px"><b style="color:{COR_TEXTO};font-size:15px">{v["ocup"]}</b>/{v["cap"]} lugares · {pct}%</span></div>'
                f'<div style="font-size:13px;color:{COR_TEXTO}">{paradas}</div>'
                f'<div style="font-size:12px;color:{COR_TEXTO_FRACO}">{_nf(v["km"], 1)} km · {v["dur"]} min</div></div>',
                unsafe_allow_html=True)
            if ver_nomes:
                col_v = "Veículo ida" if sentido == "IDA" else "Veículo volta"
                d = esc_df[(esc_df[col_v] == v["veiculo"]) & (esc_df["Turno"].str.upper() == v["turno"].upper())]
                hora = "Embarque" if sentido == "IDA" else "Desembarque"
                with st.expander(f"{len(d)} colaboradores"):
                    st.dataframe(d[["Nome", "Localidade", hora]].sort_values([hora, "Nome"]), hide_index=True, width="stretch")

        for g in ("IND", "AGR"):
            turnos_g = [(k, t) for k, t in enumerate(CFG["turnos"]) if t["grupo"] == g]
            if not turnos_g:
                continue
            st.markdown(f"### Frota {GRUPOS[g].lower()}")
            for k, t in turnos_g:
                ida = [v for v in RES["viagens"] if v["onda"] == f"T{k}_IDA"]
                volta = [v for v in RES["viagens"] if v["onda"] == f"T{k}_VOLTA"]
                if not ida and not volta:
                    continue
                with st.container(border=True):
                    st.markdown(f"**{t.get('descricao') or t['nome']}** · turno {t['nome']} · {t['entrada']} às {t['saida']}")
                    c1, c2 = st.columns(2)
                    for col, vs, s, tit, cor in ((c1, ida, "IDA", f"IDA · entrada {t['entrada']}", COR_IDA),
                                                 (c2, volta, "VOLTA", f"VOLTA · saída {t['saida']}", COR_VOLTA)):
                        with col:
                            pax, lug = sum(v["ocup"] for v in vs), sum(v["cap"] for v in vs)
                            st.markdown(
                                f'<div style="margin-bottom:6px"><span style="background:{cor};color:#fff;font-size:11.5px;'
                                f'font-weight:700;border-radius:4px;padding:2px 8px">{tit}</span> '
                                f'<span style="font-size:12.5px;color:{COR_TEXTO_FRACO}">{len(vs)} veículo(s) · {pax} pessoas'
                                + (f' em {lug} lugares ({round(100 * pax / lug)}%)' if lug else '') + '</span></div>',
                                unsafe_allow_html=True)
                            for v in sorted(vs, key=lambda v: (v["paradas"][0][1], v["veiculo"])):
                                cartao(v, s)
        st.download_button("⬇️ Baixar a escala em Excel (colaborador × veículo)",
                           excel_de({"Escala": df_escala(), "Viagens": pd.DataFrame([
                               {"Veículo": v["veiculo"], "Turno": v["turno"], "Sentido": "Ida" if v["sentido"] == "IDA" else "Volta",
                                "Roteiro": " → ".join(f"{h} {mapa.rotulo(l)}" for l, h in v["paradas"]), "Pessoas": v["ocup"],
                                "Lugares": v["cap"], "km": v["km"], "min": v["dur"]} for v in RES["viagens"]])}),
                           file_name=f"Escala_Fretamento_Nova_Produtiva_{_agora():%d%m%Y}.xlsx")

# ============================================================================= COLABORADORES
with tabs[3]:
    st.caption(f"{len(FUNCS)} colaboradores cadastrados · {sum(1 for f in FUNCS if motor.usa_fretado(f))} usam o fretado. "
               "Mudou turno, localidade ou transporte? A logística precisa ser recalculada (aba 📊 Painel).")
    if DESATUALIZADO or (FUNCS and not RES):
        faixa_calculo("colab")
    f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
    busca = f1.text_input("Buscar (nome, cadastro, função ou setor)", key="busca_func")
    ft = f2.multiselect("Turno", TURNOS, key="filtro_turno")
    fl = f3.multiselect("Localidade", LOCALIDADES, format_func=mapa.rotulo, key="filtro_loc")
    ftr = f4.multiselect("Transporte", TRANSPORTES, key="filtro_transp")
    d = df_escala()
    if not d.empty:
        d["_loc"] = [f["Localidade"] for f in FUNCS]
        if busca:
            b = busca.strip().lower()
            d = d[d.apply(lambda r: b in f"{r['Nome']} {r['Cadastro']} {r['Função']} {r['Setor']}".lower(), axis=1)]
        if ft: d = d[d["Turno"].isin(ft)]
        if fl: d = d[d["_loc"].isin(fl)]
        if ftr: d = d[d["Transporte"].isin(ftr)]
        d = d.drop(columns="_loc")
    st.dataframe(d, hide_index=True, width="stretch", height=420)
    st.caption(f"{len(d)} na lista filtrada")
    if not d.empty:
        st.download_button("⬇️ Baixar esta lista em Excel", excel_de({"Colaboradores": d}),
                           file_name=f"Colaboradores_Nova_Produtiva_{_agora():%d%m%Y}.xlsx")

    if PODE_EDITAR:
        def campos_func(pref, f=None):
            f = f or {}
            a, b = st.columns([1, 3])
            cad = a.text_input("Cadastro *", value=f.get("Cadastro", ""), key=f"{pref}_cad")
            nome = b.text_input("Nome *", value=f.get("Nome", ""), key=f"{pref}_nome")
            a, b, c = st.columns(3)
            func = a.text_input("Função", value=f.get("Funcao", ""), key=f"{pref}_func")
            setor = b.text_input("Setor", value=f.get("Setor", ""), key=f"{pref}_setor")
            cc = c.text_input("Centro de custo", value=f.get("CentroCusto", ""), key=f"{pref}_cc")
            a, b, c = st.columns(3)
            op_t = TURNOS + ([f["Turno"]] if f.get("Turno") and f["Turno"] not in TURNOS else [])
            turno = a.selectbox("Turno *", op_t, index=op_t.index(f["Turno"]) if f.get("Turno") in op_t else 0, key=f"{pref}_turno")
            op_l = LOCALIDADES + ([f["Localidade"]] if f.get("Localidade") and f["Localidade"] not in LOCALIDADES else [])
            loc = b.selectbox("Cidade / localidade *", op_l, index=op_l.index(f["Localidade"]) if f.get("Localidade") in op_l else 0,
                              format_func=mapa.rotulo, key=f"{pref}_loc")
            op_tr = TRANSPORTES + ([f["Transporte"]] if f.get("Transporte") and f["Transporte"] not in TRANSPORTES else [])
            transp = c.selectbox("Meio de transporte *", op_tr, index=op_tr.index(f["Transporte"]) if f.get("Transporte") in op_tr else 0,
                                 key=f"{pref}_transp", help="Só quem está como Fretado entra nas rotas.")
            obs = st.text_input("Observação", value=f.get("Observacao", ""), key=f"{pref}_obs")
            return dict(Title=cad.strip(), Nome=nome.strip().upper(), Funcao=func.strip().upper(), Setor=setor.strip().upper(),
                        CentroCusto=cc.strip(), Turno=turno, Localidade=loc, Transporte=transp, Observacao=obs.strip())

        def validar_func(c, ignorar_id=None):
            if not c["Title"] or not c["Nome"]:
                return "Preencha o cadastro e o nome."
            if any(f["Cadastro"] == c["Title"] and f["id"] != ignorar_id for f in FUNCS):
                return f"Já existe um colaborador com o cadastro {c['Title']}."
            return None

        st.markdown("---")
        ca, cb = st.columns(2)
        with ca:
            st.markdown("#### ➕ Novo colaborador")
            with st.form("form_novo_func", clear_on_submit=True):
                novo = campos_func("novo")
                if st.form_submit_button("Cadastrar", type="primary"):
                    erro = validar_func(novo)
                    if erro:
                        st.error(erro)
                    else:
                        _bd().criar(LISTA_FUNC, novo); invalidar()
                        st.session_state["msg"] = f"✅ {novo['Nome']} cadastrado."
                        st.rerun()
        with cb:
            st.markdown("#### ✏️ Alterar ou excluir")
            sel = st.selectbox("Colaborador", FUNCS, index=None, placeholder="Digite o nome ou o cadastro...",
                               format_func=lambda f: f"{f['Nome']} · {f['Cadastro']}", key="sel_func")
            if sel:
                with st.form(f"form_edit_{sel['id']}"):
                    alt = campos_func(f"ed{sel['id']}", sel)
                    apagar = st.checkbox("Quero EXCLUIR este colaborador")
                    b1, b2 = st.columns(2)
                    salvar = b1.form_submit_button("Salvar alterações", type="primary")
                    excluir = b2.form_submit_button("Excluir")
                if salvar:
                    erro = validar_func(alt, sel["id"])
                    if erro:
                        st.error(erro)
                    else:
                        _bd().atualizar(LISTA_FUNC, sel["id"], alt); invalidar()
                        st.session_state["msg"] = f"✅ {alt['Nome']} atualizado."
                        st.rerun()
                if excluir:
                    if not apagar:
                        st.error("Marque a caixa \"Quero EXCLUIR\" para confirmar.")
                    else:
                        _bd().apagar(LISTA_FUNC, sel["id"]); invalidar()
                        st.session_state["msg"] = f"🗑️ {sel['Nome']} excluído."
                        st.rerun()
    if st.session_state.get("msg"):
        st.success(st.session_state.pop("msg"))

# ============================================================================= FROTAS
with tabs[4]:
    st.caption("Cadastro dos veículos do fretamento (vans, micro-ônibus, ônibus...). "
               "O 📊 Painel compara os veículos ativos daqui com a frota que a logística precisa.")
    if FROTAS:
        dfr = pd.DataFrame(FROTAS).drop(columns="id").rename(columns={"Identificacao": "Identificação (placa/nº)",
                                                                       "Situacao": "Situação", "Observacao": "Observação"})
        st.dataframe(dfr, hide_index=True, width="stretch")
        ativos = [f for f in FROTAS if f["Situacao"] == "Ativo"]
        st.caption(f"{len(FROTAS)} veículos cadastrados · {len(ativos)} ativos · {sum(f['Lugares'] for f in ativos)} lugares ativos")
    else:
        st.info("Nenhum veículo cadastrado ainda.")

    if PODE_EDITAR:
        nomes_tipo = list(TIPOS_NOME.values())

        def campos_frota(pref, f=None):
            f = f or {}
            a, b, c = st.columns(3)
            ident = a.text_input("Identificação (placa ou nº) *", value=f.get("Identificacao", ""), key=f"{pref}_id")
            op = nomes_tipo + ([f["Tipo"]] if f.get("Tipo") and f["Tipo"] not in nomes_tipo else []) + ["Outro"]
            tipo = b.selectbox("Tipo *", op, index=op.index(f["Tipo"]) if f.get("Tipo") in op else 0, key=f"{pref}_tipo")
            lug_padrao = next((v["lugares"] for v in CFG["veiculos"].values() if v["nome"] == tipo), 0)
            lug = c.number_input("Lugares *", min_value=0, max_value=80, value=int(f.get("Lugares") or lug_padrao),
                                 key=f"{pref}_lug", help="Se deixar 0, vale a lotação padrão do tipo.")
            a, b, c = st.columns(3)
            prest = a.text_input("Prestador", value=f.get("Prestador", ""), key=f"{pref}_prest")
            op_f = list(GRUPOS.values())
            frota = b.selectbox("Frota *", op_f, index=op_f.index(f["Frota"]) if f.get("Frota") in op_f else 0, key=f"{pref}_frota")
            sit = c.selectbox("Situação", SITUACOES, index=SITUACOES.index(f["Situacao"]) if f.get("Situacao") in SITUACOES else 0, key=f"{pref}_sit")
            obs = st.text_input("Observação", value=f.get("Observacao", ""), key=f"{pref}_obs")
            return dict(Title=ident.strip().upper(), Tipo=tipo, Lugares=int(lug or lug_padrao), Prestador=prest.strip().upper(),
                        Frota=frota, Situacao=sit, Observacao=obs.strip())

        def validar_frota(c, ignorar_id=None):
            if not c["Title"]:
                return "Preencha a identificação (placa ou número)."
            if any(f["Identificacao"] == c["Title"] and f["id"] != ignorar_id for f in FROTAS):
                return f"Já existe um veículo com a identificação {c['Title']}."
            return None

        st.markdown("---")
        ca, cb = st.columns(2)
        with ca:
            st.markdown("#### ➕ Novo veículo")
            with st.form("form_nova_frota", clear_on_submit=True):
                novo = campos_frota("nf")
                if st.form_submit_button("Cadastrar", type="primary"):
                    erro = validar_frota(novo)
                    if erro:
                        st.error(erro)
                    else:
                        _bd().criar(LISTA_FROTAS, novo); invalidar()
                        st.session_state["msg_frota"] = f"✅ {novo['Title']} cadastrado."
                        st.rerun()
        with cb:
            st.markdown("#### ✏️ Alterar ou excluir")
            sel = st.selectbox("Veículo", FROTAS, index=None, placeholder="Escolha o veículo...",
                               format_func=lambda f: f"{f['Identificacao']} · {f['Tipo']} · {f['Frota']}", key="sel_frota")
            if sel:
                with st.form(f"form_edit_frota_{sel['id']}"):
                    alt = campos_frota(f"ef{sel['id']}", sel)
                    apagar = st.checkbox("Quero EXCLUIR este veículo")
                    b1, b2 = st.columns(2)
                    salvar = b1.form_submit_button("Salvar alterações", type="primary")
                    excluir = b2.form_submit_button("Excluir")
                if salvar:
                    erro = validar_frota(alt, sel["id"])
                    if erro:
                        st.error(erro)
                    else:
                        _bd().atualizar(LISTA_FROTAS, sel["id"], alt); invalidar()
                        st.session_state["msg_frota"] = f"✅ {alt['Title']} atualizado."
                        st.rerun()
                if excluir:
                    if not apagar:
                        st.error("Marque a caixa \"Quero EXCLUIR\" para confirmar.")
                    else:
                        _bd().apagar(LISTA_FROTAS, sel["id"]); invalidar()
                        st.session_state["msg_frota"] = f"🗑️ {sel['Identificacao']} excluído."
                        st.rerun()
    if st.session_state.get("msg_frota"):
        st.success(st.session_state.pop("msg_frota"))

# ============================================================================= TURNOS E VEÍCULOS
if PODE_EDITAR:
    with tabs[5]:
        st.caption("Estes dados entram no cálculo. Depois de salvar, recalcule a logística na aba 📊 Painel.")
        st.markdown("#### Turnos")
        st.caption("Frota: IND = industrial, AGR = agrícola (cada uma com veículos próprios). "
                   "Se trocar o NOME de um turno, altere também os colaboradores que estão nele.")
        dt = st.data_editor(pd.DataFrame(CFG["turnos"])[["nome", "descricao", "grupo", "entrada", "saida"]],
                            num_rows="dynamic", hide_index=True, width="stretch", key="ed_turnos",
                            column_config={"nome": st.column_config.TextColumn("Turno", required=True),
                                           "descricao": st.column_config.TextColumn("Descrição"),
                                           "grupo": st.column_config.SelectboxColumn("Frota", options=["IND", "AGR"], required=True),
                                           "entrada": st.column_config.TextColumn("Entrada (HH:MM)", required=True),
                                           "saida": st.column_config.TextColumn("Saída (HH:MM)", required=True)})
        st.markdown("#### Tipos de veículo")
        dvv = st.data_editor(pd.DataFrame([{"tipo": t, "nome": v["nome"], "lugares": v["lugares"], "ativo": v.get("ativo", True)}
                                           for t, v in CFG["veiculos"].items()]),
                             hide_index=True, width="stretch", key="ed_veic", disabled=["tipo", "nome"],
                             column_config={"tipo": "Código", "nome": "Tipo",
                                            "lugares": st.column_config.NumberColumn("Lugares", min_value=1, max_value=80, step=1),
                                            "ativo": st.column_config.CheckboxColumn("Usar no cálculo")})
        st.markdown("#### Regras das viagens")
        a, b, c, d2 = st.columns(4)
        viagem_max = a.number_input("Viagem máxima (min dentro do veículo)", 30, 240, int(CFG["viagem_max"]), step=5)
        desvio_max = b.number_input("Desvio máximo por passageiro (min)", 0, 120, int(CFG["desvio_max"]), step=5)
        max_paradas = c.number_input("Localidades por viagem (máx.)", 1, 5, int(CFG["max_paradas"]))
        tempo_solver = d2.number_input("Limite do otimizador (s por etapa)", 30, 600, int(CFG["tempo_solver"]), step=30)
        st.markdown("#### Tempo de embarque em cada localidade (min)")
        dp = st.data_editor(pd.DataFrame([{"localidade": l, "minutos": CFG["paradas"].get(l, CFG["parada_padrao"])} for l in LOCALIDADES]),
                            hide_index=True, width="stretch", key="ed_paradas", disabled=["localidade"],
                            column_config={"localidade": "Localidade",
                                           "minutos": st.column_config.NumberColumn("Minutos", min_value=1, max_value=40, step=1)})
        st.caption("Localidade nova (fora desta lista) precisa das distâncias por estrada: peça a inclusão ao suporte do app.")
        if st.button("💾 Salvar turnos e veículos", type="primary"):
            turnos, erros = [], []
            for _, r in dt.iterrows():
                nome = str(r["nome"] or "").strip()
                if not nome:
                    continue
                ent, sai = str(r["entrada"] or "").strip(), str(r["saida"] or "").strip()
                if not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", h) for h in (ent, sai)):
                    erros.append(f"Turno {nome}: use o horário como HH:MM (ex.: 07:30).")
                turnos.append(dict(nome=nome, grupo=r["grupo"] if r["grupo"] in GRUPOS else "IND", entrada=ent, saida=sai,
                                   descricao=str(r["descricao"] or "").strip() or f"Turno {nome}"))
            if len({t["nome"].upper() for t in turnos}) != len(turnos):
                erros.append("Há turnos com o mesmo nome.")
            if not turnos:
                erros.append("Cadastre pelo menos um turno.")
            veics = copy.deepcopy(CFG["veiculos"])
            for _, r in dvv.iterrows():
                veics[r["tipo"]]["lugares"] = int(r["lugares"]); veics[r["tipo"]]["ativo"] = bool(r["ativo"])
            if not any(v["ativo"] for v in veics.values()):
                erros.append("Deixe pelo menos um tipo de veículo no cálculo.")
            novo = dict(turnos=turnos, veiculos=veics, viagem_max=int(viagem_max), desvio_max=int(desvio_max),
                        max_paradas=int(max_paradas), tempo_solver=int(tempo_solver),
                        paradas={r["localidade"]: int(r["minutos"]) for _, r in dp.iterrows()})
            if not erros:
                try:
                    motor.montar_ondas(dict(CFG, **novo))
                except motor.ErroDados as e:
                    erros.append(str(e))
            if erros:
                for e in erros:
                    st.error(e)
            else:
                banco.gravar_config(_bd(), LISTA_CONFIG, "parametros", novo); invalidar()
                st.session_state["msg_cfg"] = "✅ Turnos e veículos salvos. Recalcule a logística na aba 📊 Painel."
                st.rerun()
        if st.session_state.get("msg_cfg"):
            st.success(st.session_state.pop("msg_cfg"))
