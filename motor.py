# -*- coding: utf-8 -*-
"""
Motor de cálculo da logística de fretamento - Usina Nova Produtiva.

Premissas:
  - só o cenário de FROTA SEPARADA (industrial e agrícola com veículos próprios);
  - sem os parâmetros do passado (horário de embarque de hoje, veículo/prestador atuais, antecipação);
  - a demanda vem do cadastro de colaboradores (quem usa o fretado), não de planilha.

Modelo (programação inteira, HiGHS via PuLP 2.9), um por frota:
  - cada troca de turno é uma "onda": IDA (quem entra) ou VOLTA (quem sai);
  - rotas candidatas = conjunto de localidades + melhor ordem + tipo de veículo + horário;
  - cada rota é um arco numa rede tempo-espaço: entre rotas o veículo espera onde está ou anda vazio até a
    próxima parada (o veículo segue a turma, não volta vazio à usina);
  - ao meio-dia não há viagem: os veículos parados nessa hora são a frota, e o dia fecha em ciclo;
  - E1 = menor frota; E2 = com essa frota, menor km (com passageiros + vazio) e menos minutos dirigindo.
"""
import bisect, hashlib, itertools, json, math
from collections import defaultdict

import pulp

# ----------------------------------------------------------------------------- PARÂMETROS PADRÃO
PADRAO = {
    # tipos de veículo: lugares, fator sobre o tempo de carro, peso de desempate (veículo menor é preferido)
    "veiculos": {
        "ONIBUS": {"nome": "Ônibus", "lugares": 44, "fator_tempo": 1.20, "peso": 600, "custo_km": 4.0, "ativo": True},
        "MICRO": {"nome": "Micro-ônibus", "lugares": 28, "fator_tempo": 1.15, "peso": 450, "custo_km": 3.0, "ativo": True},
        "VAN": {"nome": "Van", "lugares": 15, "fator_tempo": 1.10, "peso": 300, "custo_km": 2.0, "ativo": True},
    },
    # turnos: grupo = frota (IND industrial / AGR agrícola)
    "turnos": [
        {"nome": "A (4X2)", "grupo": "AGR", "entrada": "07:00", "saida": "19:00", "descricao": "Agrícola A (4x2)"},
        {"nome": "B (4X2)", "grupo": "AGR", "entrada": "19:00", "saida": "07:00", "descricao": "Agrícola B (4x2)"},
        {"nome": "A", "grupo": "IND", "entrada": "07:30", "saida": "17:00", "descricao": "Industrial/Adm A"},
        {"nome": "B", "grupo": "IND", "entrada": "16:00", "saida": "01:00", "descricao": "Industrial B"},
        {"nome": "C", "grupo": "IND", "entrada": "23:30", "saida": "07:40", "descricao": "Industrial C"},
    ],
    "chegada_antes": [30, 10],   # IDA: chega na usina entre 30 e 10 min antes da entrada
    "saida_depois": [5, 15],     # VOLTA: sai da usina de 5 a 15 min depois do fim do turno
    "desvio_max": 35,            # min a mais que a viagem direta (com paradas), por passageiro
    "viagem_max": 100,           # min dentro do veículo, no máximo
    "max_paradas": 4,
    "passo": 5,
    "parada_padrao": 5,          # min embarcando numa localidade sem tempo cadastrado
    "tempo_solver": 150,         # s por etapa (E1, E2) de cada frota
}
CUSTO_MOTORISTA_MIN = 0.6
PESO_VEICULO = 10000   # cada veículo a menos vale mais que qualquer combinação de km/motorista
USINA = "USINA"
NOME_GRUPO = {"IND": "Industrial", "AGR": "Agrícola"}
PREFIXO = {"IND": "Ind. ", "AGR": "Agr. "}


def hm(s):
    """'HH:MM' -> minutos desde 12:00 (dia operacional 12:00 -> 12:00)."""
    h, m = map(int, str(s).strip()[:5].split(":"))
    return (h * 60 + m - 720) % 1440


def fmt(t):
    t = int(round(t)) % 1440
    tt = (t + 720) % 1440
    return f"{tt // 60:02d}:{tt % 60:02d}"


def ordem_dia(t):
    """Ordena o dia a partir das 04:00."""
    return (t - hm("04:00")) % 1440


class ErroDados(Exception):
    """Problema nos dados de entrada que impede o cálculo (mensagem para o usuário)."""


# ----------------------------------------------------------------------------- ONDAS E DEMANDA
def montar_ondas(cfg):
    """turnos -> ondas {chave: dict(sentido, t0, t1, grupo, turno, desc, hora)} com as janelas no eixo 12:00."""
    ondas = {}
    a0, a1 = cfg["chegada_antes"]
    d0, d1 = cfg["saida_depois"]
    for k, t in enumerate(cfg["turnos"]):
        ent, sai = hm(t["entrada"]), hm(t["saida"])
        desc = t.get("descricao") or f"Turno {t['nome']}"
        ondas[f"T{k}_IDA"] = dict(sentido="IDA", t0=(ent - a0) % 1440, t1=(ent - a1) % 1440, grupo=t["grupo"],
                                  turno=t["nome"], desc=f"{desc} - entrada {t['entrada']}", hora=t["entrada"])
        ondas[f"T{k}_VOLTA"] = dict(sentido="VOLTA", t0=(sai + d0) % 1440, t1=(sai + d1) % 1440, grupo=t["grupo"],
                                    turno=t["nome"], desc=f"{desc} - saída {t['saida']}", hora=t["saida"])
    for k, o in ondas.items():
        if o["t1"] < o["t0"]:
            raise ErroDados(f"O turno {o['turno']} tem {'entrada' if o['sentido'] == 'IDA' else 'saída'} perto do "
                            f"meio-dia ({o['hora']}). O cálculo considera que ao meio-dia não há viagem.")
    return ondas


def usa_fretado(f):
    return str(f.get("Transporte") or "").strip().upper() == "FRETADO" and str(f.get("Ativo", "Sim")).strip().upper() != "NÃO"


def demanda_de(funcs, cfg, ondas, M):
    """{onda: {localidade: pessoas}} de quem usa o fretado. Devolve também as pendências (quem ficou de fora)."""
    por_turno = {}
    for k, t in enumerate(cfg["turnos"]):
        por_turno[t["nome"].strip().upper()] = k
    dem = {o: {} for o in ondas}
    pend = []
    for f in funcs:
        if not usa_fretado(f):
            continue
        tur = str(f.get("Turno") or "").strip().upper()
        loc = str(f.get("Localidade") or "").strip().upper()
        if tur not in por_turno:
            pend.append(dict(Cadastro=f.get("Cadastro"), Nome=f.get("Nome"), motivo=f"turno '{f.get('Turno')}' não cadastrado"))
            continue
        if loc not in M["nomes"] or loc == USINA:
            pend.append(dict(Cadastro=f.get("Cadastro"), Nome=f.get("Nome"), motivo=f"localidade '{f.get('Localidade')}' sem distâncias"))
            continue
        k = por_turno[tur]
        for s in ("IDA", "VOLTA"):
            d = dem[f"T{k}_{s}"]
            d[loc] = d.get(loc, 0) + 1
    return dem, pend


def assinatura(funcs, cfg):
    """Hash do que entra no cálculo: se mudar, a logística na tela está desatualizada."""
    base = sorted((str(f.get("Cadastro")), str(f.get("Turno") or "").strip().upper(),
                   str(f.get("Localidade") or "").strip().upper()) for f in funcs if usa_fretado(f))
    cfg2 = {k: v for k, v in cfg.items() if k != "tempo_solver"}
    txt = json.dumps([base, cfg2], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------- ROTAS CANDIDATAS
def tipos_ativos(cfg):
    return [t for t, v in cfg["veiculos"].items() if v.get("ativo", True)]


def gerar_rotas(dem, ondas, M, cfg, grupo):
    ix = {n: k for k, n in enumerate(M["nomes"])}
    T = lambda a, b: M["min"][ix[a]][ix[b]]
    K = lambda a, b: M["km"][ix[a]][ix[b]]
    parada = lambda l: cfg.get("paradas", {}).get(l, cfg["parada_padrao"])
    rotas = []
    for onda, d in dem.items():
        o = ondas[onda]
        if o["grupo"] != grupo:
            continue
        sentido = o["sentido"]
        locs = [l for l, q in d.items() if q > 0]
        horarios = list(range(o["t0"], o["t1"] + 1, cfg["passo"]))
        for n in range(1, min(cfg["max_paradas"], len(locs)) + 1):
            for S in itertools.combinations(locs, n):
                for tipo in tipos_ativos(cfg):
                    f = cfg["veiculos"][tipo]["fator_tempo"]
                    melhor = None
                    for ordem in itertools.permutations(S):
                        seq = (list(ordem) + [USINA]) if sentido == "IDA" else ([USINA] + list(ordem))
                        legs = [T(seq[k], seq[k + 1]) * f for k in range(len(seq) - 1)]
                        km = sum(K(seq[k], seq[k + 1]) for k in range(len(seq) - 1))
                        tempos, ok, acc = {}, True, 0.0
                        if sentido == "IDA":     # tempo do embarque em cada parada até a usina
                            for k in range(len(ordem) - 1, -1, -1):
                                acc += legs[k] + parada(ordem[k]); tempos[ordem[k]] = acc
                                if acc - (parada(ordem[k]) + T(ordem[k], USINA) * f) > cfg["desvio_max"] or acc > cfg["viagem_max"]: ok = False
                            total = tempos[ordem[0]]
                        else:                    # tempo da usina até o desembarque em cada parada
                            for k in range(len(ordem)):
                                acc += legs[k] + parada(ordem[k]); tempos[ordem[k]] = acc
                                if acc - (parada(ordem[k]) + T(USINA, ordem[k]) * f) > cfg["desvio_max"] or acc > cfg["viagem_max"]: ok = False
                            total = acc
                        if ok and (melhor is None or total < melhor["total"]):
                            melhor = dict(onda=onda, sentido=sentido, tipo=tipo, ordem=list(ordem), total=total,
                                          tempos=tempos, km=km, grupo=grupo)
                    if melhor is None:
                        continue
                    for h in horarios:
                        r = dict(melhor, h=h, id=len(rotas))
                        rotas.append(r)
    return rotas


def pontas(r):
    """((local, hora) em que começa com passageiros, (local, hora) em que termina). Hora no eixo 12:00 -> 12:00."""
    if r["sentido"] == "IDA":
        return (r["ordem"][0], r["h"] - r["tempos"][r["ordem"][0]]), (USINA, r["h"])
    return (USINA, r["h"]), (r["ordem"][-1], r["h"] + r["total"])


def vazio(M, cfg, a, b, tipo):
    """(minutos, km) andando vazio de a até b com esse tipo de veículo."""
    if a == b:
        return 0.0, 0.0
    i, j = M["nomes"].index(a), M["nomes"].index(b)
    return M["min"][i][j] * cfg["veiculos"][tipo]["fator_tempo"], M["km"][i][j]


# ----------------------------------------------------------------------------- MODELO
def _custo_rota(r, ondas, cfg):
    o = ondas[r["onda"]]
    espera = (o["t1"] - r["h"]) if r["sentido"] == "IDA" else (r["h"] - o["t0"])
    return r["km"] * cfg["veiculos"][r["tipo"]]["custo_km"] + CUSTO_MOTORISTA_MIN * r["total"] + 0.3 * espera


def _montar(dem, rotas, M, cfg, ondas, grupo):
    prob = pulp.LpProblem("encadeado", pulp.LpMinimize)
    cap = lambda t: cfg["veiculos"][t]["lugares"]
    y = {r["id"]: pulp.LpVariable(f"y_{r['id']}", 0, 6, cat="Integer") for r in rotas}
    x = {(r["id"], l): pulp.LpVariable(f"x_{r['id']}_{k}", 0, None, cat="Integer") for r in rotas for k, l in enumerate(r["ordem"])}
    for onda, d in dem.items():
        if ondas[onda]["grupo"] != grupo:
            continue
        for l, q in d.items():
            if q > 0:
                cobre = [x[(r["id"], l)] for r in rotas if r["onda"] == onda and l in r["ordem"]]
                if not cobre:
                    raise ErroDados(f"Nenhuma rota possível para {l} em '{ondas[onda]['desc']}' "
                                    f"(viagem acima de {cfg['viagem_max']} min?).")
                prob += pulp.lpSum(cobre) == q
    for r in rotas:
        prob += pulp.lpSum(x[(r["id"], l)] for l in r["ordem"]) <= cap(r["tipo"]) * y[r["id"]]
        for l in r["ordem"]:      # não passar numa localidade sem levar ninguém de lá
            prob += x[(r["id"], l)] >= y[r["id"]]
    F, custo_vazio, nv = {}, [], 0
    for t in tipos_ativos(cfg):
        rs = [r for r in rotas if r["tipo"] == t]
        if not rs:
            continue
        nos, starts = defaultdict(set), defaultdict(set)
        ini_em, fim_em = defaultdict(list), defaultdict(list)
        for r in rs:
            (sa, sg), (ea, ef) = pontas(r)
            sg, ef = round(sg, 2), round(ef, 2)
            if not (0 <= sg and ef < 1440):
                raise ErroDados(f"A viagem de '{ondas[r['onda']]['desc']}' atravessa o meio-dia; o cálculo não cobre esse caso.")
            nos[sa].add(sg); nos[ea].add(ef); starts[sa].add(sg)
            ini_em[(sa, sg)].append(r["id"]); fim_em[(ea, ef)].append(r["id"])
        entra, sai, sumidouro = defaultdict(list), defaultdict(list), defaultdict(list)
        for n, ids in ini_em.items(): sai[n] += [y[i] for i in ids]
        for n, ids in fim_em.items(): entra[n] += [y[i] for i in ids]
        for loc, ts in nos.items():
            ts = sorted(ts)
            f = pulp.LpVariable(f"F_{t}_{len(F)}", 0, 40, cat="Integer"); F[(t, loc)] = f
            entra[(loc, ts[0])].append(f)                       # parado ao meio-dia -> 1º evento do dia
            for a, b in zip(ts, ts[1:]):                          # espera no mesmo lugar
                w = pulp.LpVariable(f"w_{nv}", 0); nv += 1; sai[(loc, a)].append(w); entra[(loc, b)].append(w)
            w = pulp.LpVariable(f"w_{nv}", 0); nv += 1; sai[(loc, ts[-1])].append(w); sumidouro[loc].append(w)
        st = {loc: sorted(v) for loc, v in starts.items()}
        for (ea, ef) in fim_em:                                    # vazio logo depois de terminar uma viagem
            for b, tsb in st.items():
                if b == ea:
                    continue
                dmin, dkm = vazio(M, cfg, ea, b, t)
                d = pulp.LpVariable(f"d_{nv}", 0); nv += 1; sai[(ea, ef)].append(d)
                k = bisect.bisect_left(tsb, ef + dmin - 1e-6)
                if k < len(tsb): entra[(b, tsb[k])].append(d)
                else: sumidouro[b].append(d)                       # vai parar em b até o dia seguinte
                custo_vazio.append((dkm * cfg["veiculos"][t]["custo_km"] + CUSTO_MOTORISTA_MIN * dmin) * d)
        for n in set(entra) | set(sai):
            prob += pulp.lpSum(entra[n]) == pulp.lpSum(sai[n])
        for loc in nos:
            prob += pulp.lpSum(sumidouro[loc]) == F[(t, loc)]
    return prob, y, x, F, custo_vazio


class HiGHSInicio(pulp.HiGHS):
    """HiGHS que parte dos valores já gravados nas variáveis (o pulp.HiGHS da API não tem warmStart)."""
    def callSolver(self, lp):
        import highspy
        vs = lp.variables()
        if any(v.varValue is not None for v in vs):
            sol = highspy.HighsSolution()
            sol.col_value = [float(v.varValue or 0.0) for v in vs]
            sol.value_valid = True
            lp.solverModel.setSolution(sol)
        lp.solverModel.run()


def _status(prob):
    try:
        h = prob.solverModel
        info = h.getInfo()
        return h.modelStatusToString(h.getModelStatus()), float(info.mip_gap), float(info.mip_dual_bound)
    except Exception:
        return pulp.LpStatus[prob.status], None, None


def resolver_grupo(dem, M, cfg, ondas, grupo, avisar=None):
    """Resolve a frota de um grupo (IND ou AGR). Devolve dict(frota, rotas, status)."""
    diz = avisar or (lambda *_: None)
    if not any(q for o, d in dem.items() if ondas[o]["grupo"] == grupo for q in d.values()):
        return dict(frota={}, rotas=[], status=dict(vazio=True))
    rotas = gerar_rotas(dem, ondas, M, cfg, grupo)
    tempo = cfg["tempo_solver"]
    # E1: menor frota (veículo a menos vale mais que tudo; entre iguais, veículo menor)
    diz(f"{NOME_GRUPO[grupo]}: procurando a menor frota ({len(rotas)} rotas possíveis)...")
    prob, y, x, F, _ = _montar(dem, rotas, M, cfg, ondas, grupo)
    prob += pulp.lpSum((PESO_VEICULO + cfg["veiculos"][k[0]]["peso"]) * v for k, v in F.items())
    prob.solve(pulp.HiGHS(msg=False, timeLimit=tempo, gapRel=0.0))
    st1, gap1, bound1 = _status(prob)
    if pulp.LpStatus[prob.status] not in ("Optimal",) or any(v.value() is None for v in F.values()):
        raise ErroDados(f"{NOME_GRUPO[grupo]}: o otimizador não achou solução ({st1}).")
    valores = {v.name: v.varValue for v in prob.variables()}
    frota = defaultdict(int)
    for (t, loc), v in F.items():
        frota[t] += int(round(v.value() or 0))
    n1 = sum(frota.values())
    menor_peso = PESO_VEICULO + min(cfg["veiculos"][t]["peso"] for t in tipos_ativos(cfg))
    lim_inf = None if bound1 is None else max(1, int(-(-(bound1 - 1e-6) // (PESO_VEICULO + max(cfg["veiculos"][t]["peso"] for t in tipos_ativos(cfg))))))
    del menor_peso
    # E2: frota fixa, menor custo
    diz(f"{NOME_GRUPO[grupo]}: {n1} veículos; agora reduzindo km e tempo...")
    prob, y, x, F, cv = _montar(dem, rotas, M, cfg, ondas, grupo)
    for t in tipos_ativos(cfg):
        vs = [v for (tt, _), v in F.items() if tt == t]
        if vs:
            prob += pulp.lpSum(vs) <= frota.get(t, 0)
    rmap = {r["id"]: r for r in rotas}
    prob += pulp.lpSum(_custo_rota(rmap[i], ondas, cfg) * v for i, v in y.items()) + pulp.lpSum(cv)
    for v in prob.variables():
        v.varValue = valores.get(v.name)     # parte da solução da E1 (mesmos nomes)
    prob.solve(HiGHSInicio(msg=False, timeLimit=tempo, gapRel=0.0))
    st2, gap2, _ = _status(prob)
    usadas = []
    for r in rotas:
        n = int(round(y[r["id"]].value() or 0))
        if n > 0:
            usadas.append(dict(r, n=n, carga={l: int(round(x[(r["id"], l)].value() or 0)) for l in r["ordem"]}))
    frota2 = defaultdict(int)
    for (t, loc), v in F.items():
        frota2[t] += int(round(v.value() or 0))
    return dict(frota=dict(frota2), rotas=usadas, n_candidatas=len(rotas),
                status=dict(E1=dict(status=st1, gap=gap1, limite_inferior=lim_inf, frota=n1), E2=dict(status=st2, gap=gap2)))


# ----------------------------------------------------------------------------- VIAGENS E ESCALA
def expandir(rotas, cfg):
    """Cada rota com n veículos vira n viagens, dividindo a carga."""
    viagens = []
    for r in rotas:
        n = r["n"]; cap = cfg["veiculos"][r["tipo"]]["lugares"]
        cargas = [dict() for _ in range(n)]
        for l in r["ordem"]:
            q = r["carga"][l]
            for k in range(n):
                cargas[k][l] = q // n + (1 if k < q % n else 0)
        for _ in range(50):   # corrige estouro de lotação movendo passageiros entre veículos da mesma rota
            tot = [sum(c.values()) for c in cargas]
            a = max(range(n), key=lambda k: tot[k]); b = min(range(n), key=lambda k: tot[k])
            if tot[a] <= cap: break
            l = max(cargas[a], key=lambda l: cargas[a][l]); cargas[a][l] -= 1; cargas[b][l] += 1
        for k in range(n):
            v = dict(r); v["carga"] = cargas[k]; v["ocup"] = sum(cargas[k].values()); viagens.append(v)
    return viagens


def escala(viagens, M, cfg):
    """Encadeia as viagens em itinerários diários FIXOS (o veículo repete o mesmo dia todo dia) com o menor km
    vazio. Grava v['veiculo'] e devolve {veiculo: [segmentos]}. Segmento: dict(tipo, ini, fim, de, para, km)."""
    for v in viagens: v["veiculo"] = None
    grupos = defaultdict(list)
    for v in viagens: grupos[(v["grupo"], v["tipo"])].append(v)
    saida = {}
    for (grupo, tipo), vs in sorted(grupos.items()):
        n = len(vs); P = [pontas(v) for v in vs]
        ckm = cfg["veiculos"][tipo]["custo_km"]

        def vira(i, j):
            """Quantas vezes o veículo passa pelo meio-dia entre a viagem i e a j (cada passagem = um veículo)."""
            ea, ef = P[i][1]; sa, sg = P[j][0]; dmin, _ = vazio(M, cfg, ea, sa, tipo)
            c = 1 if (i == j or sg < ef) else 0
            if (sg - ef) % 1440 < dmin - 1e-6: c += 1                   # não dá tempo: só no dia seguinte
            return c
        prob = pulp.LpProblem("escala", pulp.LpMinimize); z, obj = {}, []
        for i in range(n):
            ea, ef = P[i][1]
            for j in range(n):
                sa, sg = P[j][0]
                dmin, dkm = vazio(M, cfg, ea, sa, tipo)
                z[i, j] = pulp.LpVariable(f"z_{i}_{j}", 0, 1)
                obj.append(z[i, j] * (PESO_VEICULO * vira(i, j) + dkm * ckm + CUSTO_MOTORISTA_MIN * dmin))
        for i in range(n):
            prob += pulp.lpSum(z[i, j] for j in range(n)) == 1
            prob += pulp.lpSum(z[j, i] for j in range(n)) == 1
        prob += pulp.lpSum(obj)
        prob.solve(pulp.HiGHS(msg=False))
        suc = {i: max(range(n), key=lambda j: z[i, j].value() or 0) for i in range(n)}

        # Um ciclo que passa k vezes pelo meio-dia = k veículos revezando um roteiro de k dias. Para cada veículo
        # repetir o MESMO dia, o ciclo é cortado num horário em que as k cópias estão paradas (o corte mais barato).
        def cortar(c, k):
            m = len(c); off = 0; ab = []
            for idx in range(m):
                if idx > 0: off += 1440 * vira(c[idx - 1], c[idx])
                ab.append((P[c[idx]][0][1] + off, P[c[idx]][1][1] + off))
            tot = k * 1440; a0 = ab[0][0]; esperas = []
            for idx in range(m):
                i, j = c[idx], c[(idx + 1) % m]
                dmin, _ = vazio(M, cfg, P[i][1][0], P[j][0][0], tipo)
                prox = ab[(idx + 1) % m][0] + (tot if idx == m - 1 else 0)
                esperas.append((P[i][1][0], ab[idx][1], prox - dmin))

            def onde(t):
                t = a0 + (t - a0) % tot
                return next((loc for loc, a, b in esperas if a - 1e-6 <= t <= b + 1e-6), None)

            def km_pedaco(it):                                          # km vazio do dia, fechando nele mesmo
                tot_km = 0.0
                for a, b in zip(it, it[1:] + it[:1]):
                    dmin, dkm = vazio(M, cfg, P[a][1][0], P[b][0][0], tipo)
                    if (P[b][0][1] - P[a][1][1]) % 1440 < dmin - 1e-6: return None
                    tot_km += dkm
                return tot_km
            melhor = None
            cands = {0.0} | {(a + f * (b - a)) % 1440 for _, a, b in esperas if b - a >= 1 for f in (0.1, 0.5, 0.9)}
            for T in sorted(cands):
                T1 = a0 + (T - a0) % 1440
                if None in [onde(T1 + d * 1440) for d in range(k)]: continue    # alguma cópia em viagem
                copias = defaultdict(list)
                for idx in sorted(range(m), key=lambda q: (ab[q][0] - T1) % tot):
                    copias[int(((ab[idx][0] - T1) % tot) // 1440)].append(c[idx])
                if len(copias) != k: continue
                kms = [km_pedaco(it) for it in copias.values()]
                if None in kms: continue
                if melhor is None or sum(kms) < melhor[0] - 1e-6: melhor = (sum(kms), list(copias.values()))
            return melhor[1] if melhor else None
        vistos, itins = set(), []
        for i0 in range(n):
            if i0 in vistos: continue
            c, j = [i0], suc[i0]; vistos.add(i0)
            while j != i0: c.append(j); vistos.add(j); j = suc[j]
            k = sum(vira(c[q], c[(q + 1) % len(c)]) for q in range(len(c)))
            pedacos = [c] if k <= 1 else cortar(c, k)
            if pedacos is None:                                         # corta no meio-dia
                pedacos = []
                for q in range(len(c)):
                    if vira(c[q - 1], c[q]):
                        it, r = [c[q]], q
                        while not vira(c[r % len(c)], c[(r + 1) % len(c)]): r += 1; it.append(c[r % len(c)])
                        pedacos.append(it)
            itins += pedacos
        assert sorted(q for it in itins for q in it) == list(range(n))
        itins.sort(key=lambda it: min(ordem_dia(P[k][0][1]) for k in it))
        for num, it in enumerate(itins, 1):
            nome = f"{PREFIXO.get(grupo, '')}{cfg['veiculos'][tipo]['nome']} {num}"
            segs = []
            for a, b in zip(it, it[1:] + it[:1]):                     # fecha o ciclo no próprio início (dia seguinte)
                (sa, sg), (ea, ef) = P[a]
                vs[a]["veiculo"] = nome
                segs.append(dict(tipo="VIAGEM", ini=sg, fim=ef, de=sa, para=ea, km=vs[a]["km"]))
                nsa, nsg = P[b][0]
                dmin, dkm = vazio(M, cfg, ea, nsa, tipo)
                if dkm > 0 or ea != nsa:                               # espera onde terminou e sai a tempo
                    segs.append(dict(tipo="VAZIO", ini=(nsg - dmin) % 1440, fim=nsg % 1440, de=ea, para=nsa, km=dkm))
            segs.sort(key=lambda s: ordem_dia(s["ini"]))
            saida[nome] = segs
    return saida


def horarios(v, cfg):
    """Horário em cada parada (embarque na IDA, desembarque na VOLTA)."""
    parada = lambda l: cfg.get("paradas", {}).get(l, cfg["parada_padrao"])
    out = []
    if v["sentido"] == "IDA":
        for l in v["ordem"]: out.append((l, v["h"] - v["tempos"][l]))
        out.append((USINA, v["h"]))
    else:
        out.append((USINA, v["h"]))
        for l in v["ordem"]: out.append((l, v["h"] + v["tempos"][l] - parada(l)))
    return out


def alocar_pessoas(funcs, cfg, viagens, ondas):
    """Distribui os colaboradores de cada onda/localidade entre as viagens que atendem.
    Devolve {cadastro: {"IDA": {...}, "VOLTA": {...}}} com veículo e horário da parada."""
    por_turno = {t["nome"].strip().upper(): k for k, t in enumerate(cfg["turnos"])}
    fila = defaultdict(list)
    for f in sorted((f for f in funcs if usa_fretado(f)), key=lambda f: str(f.get("Nome") or "")):
        tur = str(f.get("Turno") or "").strip().upper(); loc = str(f.get("Localidade") or "").strip().upper()
        if tur in por_turno:
            for s in ("IDA", "VOLTA"):
                fila[(f"T{por_turno[tur]}_{s}", loc)].append(str(f.get("Cadastro")))
    aloc = defaultdict(dict)
    for (onda, loc), pessoas in fila.items():
        vs = sorted([v for v in viagens if v["onda"] == onda and v["carga"].get(loc, 0) > 0], key=lambda v: v["veiculo"])
        i = 0
        for v in vs:
            hora = dict(horarios(v, cfg))[loc]
            for _ in range(v["carga"][loc]):
                if i < len(pessoas):
                    aloc[pessoas[i]][ondas[onda]["sentido"]] = dict(veiculo=v["veiculo"], hora=fmt(hora), usina=fmt(v["h"]))
                    i += 1
    return dict(aloc)


# ----------------------------------------------------------------------------- CÁLCULO COMPLETO
def calcular(funcs, M, cfg=None, avisar=None):
    """Roda tudo e devolve o resultado pronto para gravar (JSON) e mostrar."""
    cfg = cfg or PADRAO
    ondas = montar_ondas(cfg)
    dem, pend = demanda_de(funcs, cfg, ondas, M)
    grupos = sorted({t["grupo"] for t in cfg["turnos"]}, key=lambda g: 0 if g == "IND" else 1)
    partes, viagens = {}, []
    for g in grupos:
        partes[g] = resolver_grupo(dem, M, cfg, ondas, g, avisar)
        viagens += expandir(partes[g]["rotas"], cfg)
    esc = escala(viagens, M, cfg)
    aloc = alocar_pessoas(funcs, cfg, viagens, ondas)
    out_v = []
    for v in viagens:
        (sa, sg), (ea, ef) = pontas(v)
        out_v.append(dict(veiculo=v["veiculo"], tipo=v["tipo"], grupo=v["grupo"], onda=v["onda"], sentido=v["sentido"],
                          turno=ondas[v["onda"]]["turno"], ondaDesc=ondas[v["onda"]]["desc"], ordem=v["ordem"],
                          carga=v["carga"], ocup=v["ocup"], cap=cfg["veiculos"][v["tipo"]]["lugares"],
                          paradas=[[l, fmt(t)] for l, t in horarios(v, cfg)], ini=fmt(sg), fim=fmt(ef),
                          iniMin=round(ordem_dia(sg), 1), fimMin=round(ordem_dia(ef), 1), km=round(v["km"], 1),
                          dur=round(v["total"])))
    out_v.sort(key=lambda v: (v["iniMin"], v["veiculo"]))
    vazios = [dict(veiculo=n, de=s["de"], para=s["para"], km=round(s["km"], 1), ini=fmt(s["ini"]), fim=fmt(s["fim"]),
                   iniMin=round(ordem_dia(s["ini"]), 1), fimMin=round(ordem_dia(s["fim"]), 1))
              for n, ss in esc.items() for s in ss if s["tipo"] == "VAZIO"]
    frota = {g: {t: n for t, n in partes[g]["frota"].items() if n} for g in grupos}
    veiculos = []
    for nome in esc:
        vs = [v for v in out_v if v["veiculo"] == nome]
        vz = [z for z in vazios if z["veiculo"] == nome]
        veiculos.append(dict(nome=nome, tipo=vs[0]["tipo"], grupo=vs[0]["grupo"], lugares=vs[0]["cap"], viagens=len(vs),
                             km=round(sum(v["km"] for v in vs), 1), kmVazio=round(sum(z["km"] for z in vz), 1),
                             pax=sum(v["ocup"] for v in vs), assentos=sum(v["cap"] for v in vs)))
    km_pax = sum(v["km"] for v in out_v); km_vazio = sum(z["km"] for z in vazios)
    assentos = sum(v["cap"] for v in out_v); pax = sum(v["ocup"] for v in out_v)
    resumo = dict(veiculos=len(veiculos), viagens=len(out_v), kmPax=round(km_pax, 1), kmVazio=round(km_vazio, 1),
                  kmTotal=round(km_pax + km_vazio, 1), assentos=assentos, pax=pax,
                  ocupacao=round(100 * pax / assentos, 1) if assentos else 0,
                  pessoas=len({c for c in aloc}), lugaresFrota=sum(v["lugares"] for v in veiculos))
    return dict(assinatura=assinatura(funcs, cfg), resumo=resumo, frota=frota, veiculos=veiculos, viagens=out_v,
                vazios=vazios, alocacao=aloc, pendencias=pend, demanda=dem,
                ondas={k: dict(sentido=o["sentido"], grupo=o["grupo"], turno=o["turno"], desc=o["desc"], hora=o["hora"])
                       for k, o in ondas.items()},
                status={g: partes[g]["status"] for g in grupos})
