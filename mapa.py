# -*- coding: utf-8 -*-
"""Página HTML do mapa das rotas + escala dos veículos (tema claro, dourado).
Não leva nomes de colaboradores: só localidades, veículos, horários e quantidades."""
import json

ROTULO_LOC = {"ASTORGA": "Astorga", "SANTA ZELIA": "Santa Zélia", "IÇARA": "Içara", "TUPINAMBA": "Tupinambá",
              "MUNHOZ DE MELO": "Munhoz de Mello", "JAGUAPITÃ": "Jaguapitã", "FLORESTOPOLIS": "Florestópolis",
              "PORECATU": "Porecatu", "ARAPONGAS": "Arapongas", "USINA": "Usina"}


def rotulo(loc):
    return ROTULO_LOC.get(loc, str(loc).title())


def dados_mapa(res, M, GEO, cfg):
    """Junta o resultado do cálculo com os traçados das estradas."""
    def geom(v):
        seq = (v["ordem"] + ["USINA"]) if v["sentido"] == "IDA" else (["USINA"] + v["ordem"])
        pts = []
        for a, b in zip(seq, seq[1:]):
            pts += GEO.get(f"{a}|{b}") or [M["pontos"][a], M["pontos"][b]]
        return pts
    usados = {"USINA"} | {l for v in res["viagens"] for l in v["ordem"]}
    pessoas = {}
    for onda, d in res["demanda"].items():
        if res["ondas"][onda]["sentido"] == "IDA":
            for l, q in d.items():
                pessoas[l] = pessoas.get(l, 0) + q
    usados |= set(pessoas)
    malha, vistos = [], set()
    for k, pts in GEO.items():
        a, b = k.split("|")
        chave = tuple(sorted((a, b)))
        if chave in vistos or a not in usados or b not in usados:
            continue
        vistos.add(chave); malha.append(pts[::2] + [pts[-1]])
    turnos = []
    for k, t in enumerate(cfg["turnos"]):
        turnos.append(dict(nome=t["nome"], grupo=t["grupo"], desc=t.get("descricao") or t["nome"], entrada=t["entrada"],
                           saida=t["saida"], ida=f"T{k}_IDA", volta=f"T{k}_VOLTA"))
    return dict(pontos={k: v for k, v in M["pontos"].items() if k in usados},
                rotulos={k: rotulo(k) for k in M["pontos"]}, pessoas=pessoas, malha=malha, turnos=turnos,
                viagens=[dict(v, geo=geom(v)) for v in res["viagens"]], vazios=res["vazios"], ondas=res["ondas"],
                resumo=res["resumo"], frota=res["frota"],
                nomesTipo={t: v["nome"] for t, v in cfg["veiculos"].items()})


HTML = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap">
<style>
/* Quadro de despacho: resumo no topo, mapa + lista de viagens lado a lado, escala de cada veículo embaixo.
   Cores do Controle de Frotas (tema claro, dourado). */
:root {
  --bg: #F7F5EF; --panel: #FFFFFF; --panel-2: #F1EDE2; --ink: #1F1C14; --ink-2: #4c4a45; --muted: #6B6556;
  --line: #DED7BA; --road: #d9d6cd; --accent: #9B8328; --accent-ink: #ffffff; --ida: #2a78d6; --volta: #eb6834;
  --v1: #2a78d6; --v2: #eb6834; --v3: #1baf7a; --v4: #eda100; --v5: #e87ba4; --v6: #008300; --v7: #6250d6; --v8: #e34948;
  --f-display: "Space Grotesk", "Segoe UI", system-ui, sans-serif;
  --f-body: "Inter", "Segoe UI", system-ui, sans-serif;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--f-body); margin: 0; padding: 6px 2px 24px; }
.wrap { max-width: 1320px; margin: 0 auto; display: grid; gap: 22px; grid-template-columns: minmax(0, 1fr); }
.wrap > *, .sec > * { min-width: 0; }
h2 { font-family: var(--f-display); margin: 0; letter-spacing: -0.01em; font-size: 20px; font-weight: 600; }
.num { font-variant-numeric: tabular-nums; }

.kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-left: 5px solid var(--accent); border-radius: 10px; padding: 12px 14px; display: grid; gap: 2px; min-width: 0; }
.kpi .lab { font-size: 12.5px; color: var(--muted); }
.kpi .val { font-family: var(--f-display); font-size: 28px; font-weight: 600; line-height: 1.1; }
.kpi .sub { font-size: 12.5px; color: var(--ink-2); }

.turnos { display: grid; gap: 6px; }
.tl { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 10px; align-items: center; }
.tl .nome { font-size: 13px; font-weight: 600; }
.tl .nome small { display: block; font-weight: 400; color: var(--muted); font-size: 11.5px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: 500 12.5px var(--f-body); color: var(--ink-2); background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 5px 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.chip i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.chip[aria-pressed="true"] { border-color: var(--accent); color: var(--ink); background: #EFE6C4; font-weight: 600; }
.chip:focus-visible, .trip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.chip:disabled { opacity: .45; cursor: default; }

.board { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px; min-width: 0; }
#mapa { width: 100%; height: auto; display: block; }
.legend-map { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 8px; }
.legend-map span { display: inline-flex; align-items: center; gap: 6px; }
.trips { display: grid; gap: 8px; }
.trip { display: grid; grid-template-columns: 12px minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); cursor: default; }
.trip.dim { opacity: 0.45; }
.trip .sw { width: 12px; height: 12px; border-radius: 3px; margin-top: 4px; }
.trip .t1 { font-weight: 600; font-size: 14px; }
.trip .t2 { color: var(--ink-2); font-size: 13px; }
.trip .stops { font-size: 12.5px; color: var(--ink-2); display: flex; flex-wrap: wrap; gap: 2px 10px; margin-top: 4px; }
.trip .stops b { color: var(--ink); font-weight: 600; }
.occ { text-align: right; font-size: 12.5px; color: var(--muted); min-width: 74px; }
.occ .n { font-family: var(--f-display); font-size: 18px; color: var(--ink); font-weight: 600; }
.meter { height: 6px; background: var(--panel-2); border-radius: 3px; margin-top: 4px; overflow: hidden; }
.meter i { display: block; height: 100%; border-radius: 3px; background: var(--accent); }
.wave-h { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; font-weight: 600; margin: 6px 0 0; display: flex; align-items: center; gap: 8px; }
.tag { font-size: 11px; font-weight: 700; color: #fff; border-radius: 4px; padding: 1px 7px; letter-spacing: .05em; }

.gantt-wrap { overflow-x: auto; }
#gantt { display: block; min-width: 760px; width: 100%; }
.tip { position: fixed; pointer-events: none; z-index: 20; background: var(--ink); color: #fff; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; max-width: 300px; box-shadow: 0 6px 24px rgba(0,0,0,.25); }
.tip b { font-weight: 600; }
.tip .m { color: #d8d3c2; }
.sec { display: grid; gap: 12px; grid-template-columns: minmax(0, 1fr); }
.sec-h { display: flex; justify-content: space-between; align-items: end; gap: 12px; flex-wrap: wrap; }
.small { font-size: 12.5px; color: var(--muted); }
@media (max-width: 900px) { .board { grid-template-columns: minmax(0, 1fr); } .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } .tl { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-motion: no-preference) { .trip, path.rota { transition: opacity .15s; } }
</style></head><body>

<div class="wrap">
  <section class="kpis" id="kpis"></section>

  <section class="sec">
    <div class="sec-h">
      <h2>Rotas por turno: ida e volta</h2>
      <span class="small">Escolha o turno e o sentido · passe o mouse numa rota para destacar</span>
    </div>
    <div class="turnos" id="turnos"></div>
    <div class="board">
      <div class="panel">
        <svg id="mapa" role="img" aria-label="Mapa das rotas"></svg>
        <div class="legend-map">
          <span><svg width="14" height="14" aria-hidden="true"><path d="M7 1 L8.8 5.2 L13.3 5.4 L9.8 8.3 L11 12.7 L7 10.2 L3 12.7 L4.2 8.3 L0.7 5.4 L5.2 5.2 Z" fill="var(--accent)"/></svg>Usina</span>
          <span><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="var(--panel-2)" stroke="var(--ink-2)" stroke-width="1.5"/></svg>Localidade (tamanho = nº de colaboradores)</span>
          <span><svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" stroke="var(--road)" stroke-width="3"/></svg>Estradas usadas no cálculo</span>
        </div>
      </div>
      <div class="trips" id="lista"></div>
    </div>
  </section>

  <section class="sec">
    <div class="sec-h">
      <h2>O dia de cada veículo</h2>
      <span class="small">Barra cheia = com passageiros · barra clara = vazio até a próxima turma · clique numa barra para ver a rota</span>
    </div>
    <div class="panel gantt-wrap"><svg id="gantt" role="img" aria-label="Escala dos veículos ao longo do dia"></svg></div>
  </section>
</div>
<div class="tip" id="tip" hidden></div>

<script>
const D = __DADOS__;
const ROT = D.rotulos, P = D.pontos, NT = D.nomesTipo, R = D.resumo;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const nf = (n, d = 0) => Number(n).toLocaleString("pt-BR", {minimumFractionDigits: d, maximumFractionDigits: d});
const svgNS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(svgNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const temViagem = o => D.viagens.some(v => v.onda === o);
let ondaSel = (D.turnos.map(t => t.ida).find(temViagem)) || (D.viagens[0] || {}).onda;

// cores fixas por veículo (industrial primeiro, do maior para o menor)
const nomesV = [...new Set(D.viagens.map(v => v.veiculo))];
const ordemTipo = Object.keys(NT);
nomesV.sort((a, b) => { const va = D.viagens.find(v => v.veiculo === a), vb = D.viagens.find(v => v.veiculo === b);
  return (va.grupo === "IND" ? 0 : 1) - (vb.grupo === "IND" ? 0 : 1) || ordemTipo.indexOf(va.tipo) - ordemTipo.indexOf(vb.tipo) || a.localeCompare(b, "pt"); });
const cor = {}; nomesV.forEach((n, i) => cor[n] = `var(--v${(i % 8) + 1})`);

function resumo() {
  const comp = Object.entries(D.frota).map(([g, f]) => (g === "IND" ? "Ind.: " : "Agr.: ") + (Object.entries(f).map(([t, n]) => `${n} ${NT[t].toLowerCase()}`).join(", ") || "nenhum")).join(" · ");
  $("#kpis").innerHTML = [
    ["Veículos no total", nf(R.veiculos), comp],
    ["Ocupação (pessoas ÷ lugares)", nf(R.ocupacao, 1) + "%", `${nf(R.pax)} pessoas em ${nf(R.assentos)} lugares, somando as viagens`],
    ["Viagens por dia", nf(R.viagens), `${nf(R.pessoas)} colaboradores no fretado`],
    ["km total por dia", nf(R.kmTotal), `${nf(R.kmPax)} km com passageiros`],
    ["km vazio por dia", nf(R.kmVazio), R.kmTotal ? `${nf(100 * R.kmVazio / R.kmTotal, 1)}% do km total` : ""],
  ].map(([l, v, s]) => `<div class="kpi"><div class="lab">${l}</div><div class="val num">${v}</div><div class="sub">${esc(s)}</div></div>`).join("");
}

let proj, mapG;
function montarMapa() {
  const svg = $("#mapa"); svg.innerHTML = "";
  const lats = [], lons = [];
  for (const k in P) { lats.push(P[k][0]); lons.push(P[k][1]); }
  const la0 = Math.max(...lats) + 0.03, la1 = Math.min(...lats) - 0.03, lo0 = Math.min(...lons) - 0.16, lo1 = Math.max(...lons) + 0.17;
  const kx = Math.cos(23.1 * Math.PI / 180), W = 560, sc = W / ((lo1 - lo0) * kx), H = (la0 - la1) * sc;
  proj = (la, lo) => [((lo - lo0) * kx * sc).toFixed(1), ((la0 - la) * sc).toFixed(1)];
  svg.setAttribute("viewBox", `0 0 ${W} ${H.toFixed(0)}`);
  const gR = el("g", {}, svg);
  for (const pts of D.malha) el("path", {d: "M" + pts.map(p => proj(p[0], p[1]).join(",")).join("L"), fill: "none", stroke: "var(--road)", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round"}, gR);
  mapG = el("g", {}, svg);
  const gP = el("g", {}, svg);
  const maxP = Math.max(1, ...Object.values(D.pessoas));
  const lonU = P.USINA[1];
  for (const k in P) {
    if (k === "USINA") continue;
    const [x, y] = proj(P[k][0], P[k][1]); const n = D.pessoas[k] || 0; const r = 4 + 10 * Math.sqrt(n / maxP);
    el("circle", {cx: x, cy: y, r: r.toFixed(1), fill: "var(--panel-2)", stroke: "var(--ink-2)", "stroke-width": 1.5}, gP);
    const dx = P[k][1] > lonU ? 1 : -1;
    const t = el("text", {x: (+x + dx * (r + 5)).toFixed(1), y: (+y + 4).toFixed(1), "text-anchor": dx > 0 ? "start" : "end", fill: "var(--ink)", "font-size": 12.5, "font-weight": 600, "font-family": "var(--f-body)"}, gP);
    t.textContent = ROT[k];
    const t2 = el("text", {x: (+x + dx * (r + 5)).toFixed(1), y: (+y + 18).toFixed(1), "text-anchor": dx > 0 ? "start" : "end", fill: "var(--muted)", "font-size": 11, "font-family": "var(--f-body)"}, gP);
    t2.textContent = `${n} pessoas`;
  }
  const [ux, uy] = proj(P.USINA[0], P.USINA[1]);
  const star = (cx, cy, R) => { let d = ""; for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? R * 0.45 : R; d += (i ? "L" : "M") + (cx + rr * Math.cos(a)).toFixed(1) + "," + (cy + rr * Math.sin(a)).toFixed(1); } return d + "Z"; };
  el("path", {d: star(+ux, +uy, 11), fill: "var(--accent)", stroke: "var(--panel)", "stroke-width": 2}, gP);
  const tu = el("text", {x: +ux + 14, y: +uy - 8, fill: "var(--accent)", "font-size": 13, "font-weight": 700, "font-family": "var(--f-display)"}, gP); tu.textContent = "Usina Nova Produtiva";
  const km10 = 10 / 111 * sc; const sy = H - 14;
  el("line", {x1: 16, y1: sy, x2: (16 + km10).toFixed(1), y2: sy, stroke: "var(--muted)", "stroke-width": 2}, svg);
  const ts = el("text", {x: 16, y: sy - 6, fill: "var(--muted)", "font-size": 11, "font-family": "var(--f-body)"}, svg); ts.textContent = "10 km";
}

const viagensSel = () => D.viagens.filter(v => v.onda === ondaSel);

function desenharRotas() {
  mapG.innerHTML = "";
  const vs = viagensSel();
  vs.forEach((v, i) => {
    const d = "M" + v.geo.map(p => proj(p[0], p[1]).join(",")).join("L");
    el("path", {d, fill: "none", stroke: "var(--panel)", "stroke-width": 7, "stroke-linejoin": "round", "stroke-linecap": "round", class: "rota halo", "data-i": i}, mapG);
    const p = el("path", {d, fill: "none", stroke: cor[v.veiculo], "stroke-width": 3.5, "stroke-linejoin": "round", "stroke-linecap": "round", class: "rota", "data-i": i}, mapG);
    p.addEventListener("mouseenter", e => destacar(i, e)); p.addEventListener("mousemove", e => moverTip(e)); p.addEventListener("mouseleave", () => destacar(null));
  });
  const L = $("#lista"); L.innerHTML = "";
  if (!vs.length) { L.innerHTML = '<div class="small">Nenhuma viagem nesta troca de turno.</div>'; return; }
  const o = D.ondas[ondaSel], ida = o.sentido === "IDA";
  const pax = vs.reduce((s, v) => s + v.ocup, 0), lug = vs.reduce((s, v) => s + v.cap, 0);
  const h = document.createElement("div"); h.className = "wave-h";
  h.innerHTML = `<span class="tag" style="background:var(--${ida ? "ida" : "volta"})">${ida ? "IDA" : "VOLTA"}</span>${esc(o.desc)} · ${pax} pessoas em ${lug} lugares (${Math.round(100 * pax / lug)}%) · ${vs.length} veículo${vs.length > 1 ? "s" : ""}`;
  L.appendChild(h);
  vs.map((v, i) => [v, i]).sort((a, b) => a[0].paradas[0][1].localeCompare(b[0].paradas[0][1]) || a[0].veiculo.localeCompare(b[0].veiculo, "pt")).forEach(([v, i]) => {
    const div = document.createElement("div"); div.className = "trip"; div.tabIndex = 0; div.dataset.i = i;
    const stops = v.paradas.map(([l, t]) => `<span><b class="num">${t}</b> ${esc(ROT[l])}${l !== "USINA" ? ` (${v.carga[l]})` : ""}</span>`).join("");
    div.innerHTML = `<span class="sw" style="background:${cor[v.veiculo]}"></span>
      <div><div class="t1">${esc(v.veiculo)}</div><div class="t2">${ida ? "Embarque → chegada na usina" : "Saída da usina → desembarque"} · ${nf(v.km, 1)} km · ${v.dur} min</div><div class="stops">${stops}</div></div>
      <div class="occ"><span class="n num">${v.ocup}</span>/${v.cap}<div class="meter"><i style="width:${Math.min(100, Math.round(100 * v.ocup / v.cap))}%"></i></div>${Math.round(100 * v.ocup / v.cap)}%</div>`;
    div.addEventListener("mouseenter", () => destacar(i)); div.addEventListener("mouseleave", () => destacar(null));
    div.addEventListener("focus", () => destacar(i)); div.addEventListener("blur", () => destacar(null));
    L.appendChild(div);
  });
}

function destacar(i, ev) {
  document.querySelectorAll("#mapa path.rota").forEach(p => { p.style.opacity = (i === null || +p.dataset.i === i) ? 1 : 0.15; });
  document.querySelectorAll("#lista .trip").forEach(t => t.classList.toggle("dim", i !== null && +t.dataset.i !== i));
  if (i !== null) { const top = [...document.querySelectorAll(`#mapa path.rota[data-i="${i}"]`)]; top.forEach(p => p.parentNode.appendChild(p)); }
  const tip = $("#tip");
  if (i === null || !ev) { tip.hidden = true; return; }
  const v = viagensSel()[i];
  tip.innerHTML = `<b>${esc(v.veiculo)}</b> <span class="m">· ${v.ocup}/${v.cap} lugares</span><br>${v.paradas.map(([l, t]) => `${t} ${esc(ROT[l])}`).join(" → ")}`;
  tip.hidden = false; moverTip(ev);
}
function moverTip(e) { const t = $("#tip"); const x = Math.min(e.clientX + 14, window.innerWidth - t.offsetWidth - 8); t.style.left = x + "px"; t.style.top = (e.clientY + 14) + "px"; }

// uma linha por turno, com a ida e a volta lado a lado
function chips() {
  const C = $("#turnos"); C.innerHTML = "";
  for (const t of D.turnos) {
    const linha = document.createElement("div"); linha.className = "tl";
    linha.innerHTML = `<div class="nome">${esc(t.desc)}<small>${t.grupo === "IND" ? "Frota industrial" : "Frota agrícola"} · ${t.entrada}–${t.saida}</small></div>`;
    const cs = document.createElement("div"); cs.className = "chips";
    for (const [onda, rot, c] of [[t.ida, `Ida · entrada ${t.entrada}`, "ida"], [t.volta, `Volta · saída ${t.saida}`, "volta"]]) {
      const vs = D.viagens.filter(v => v.onda === onda);
      const b = document.createElement("button"); b.className = "chip"; b.setAttribute("aria-pressed", onda === ondaSel); b.disabled = !vs.length;
      b.innerHTML = `<i style="background:var(--${c})"></i>${rot} · ${vs.length} veíc. · ${vs.reduce((s, v) => s + v.ocup, 0)} pessoas`;
      b.addEventListener("click", () => { ondaSel = onda; chips(); desenharRotas(); });
      cs.appendChild(b);
    }
    linha.appendChild(cs); C.appendChild(linha);
  }
}

function gantt() {
  const svg = $("#gantt"); svg.innerHTML = "";
  const vs = D.viagens, nomes = nomesV;
  const L = 170, Rg = 16, T = 30, RH = 34, W = 1180, H = T + nomes.length * RH + 10;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const x = m => L + (W - L - Rg) * m / 1440;   // m = minutos desde 04:00
  for (let h = 0; h <= 24; h += 2) {
    const xx = x(h * 60);
    el("line", {x1: xx, y1: T - 6, x2: xx, y2: H - 6, stroke: "var(--line)", "stroke-width": 1}, svg);
    const t = el("text", {x: xx, y: T - 12, "text-anchor": "middle", fill: "var(--muted)", "font-size": 11.5, "font-family": "var(--f-body)"}, svg);
    t.textContent = String((4 + h) % 24).padStart(2, "0") + "h";
  }
  nomes.forEach((n, k) => {
    const y = T + k * RH;
    const t = el("text", {x: 0, y: y + RH / 2 + 4, fill: "var(--ink)", "font-size": 13, "font-weight": 600, "font-family": "var(--f-body)"}, svg); t.textContent = n;
    el("line", {x1: L, y1: y + RH, x2: W - Rg, y2: y + RH, stroke: "var(--line)", "stroke-width": 1}, svg);
    for (const v of vs.filter(v => v.veiculo === n)) {
      let a = v.iniMin, b = v.fimMin; if (b < a) b += 1440;
      const g = el("g", {style: "cursor:pointer"}, svg);
      el("rect", {x: x(a), y: y + 6, width: Math.max(2, x(b) - x(a) - 1), height: RH - 12, rx: 4, fill: cor[n]}, g);
      el("rect", {x: x(a) - 2, y: y, width: x(b) - x(a) + 4, height: RH, fill: "transparent"}, g);
      g.addEventListener("mousemove", e => {
        const tip = $("#tip");
        tip.innerHTML = `<b>${esc(n)}</b> · ${v.sentido === "IDA" ? "IDA" : "VOLTA"} · ${esc(v.ondaDesc)}<br>${v.paradas.map(([l, t]) => `${t} ${esc(ROT[l])}`).join(" → ")}<br><span class="m">${v.ocup}/${v.cap} lugares · ${nf(v.km, 1)} km com passageiros</span>`;
        tip.hidden = false; moverTip(e);
      });
      g.addEventListener("mouseleave", () => $("#tip").hidden = true);
      g.addEventListener("click", () => { ondaSel = v.onda; chips(); desenharRotas(); $("#turnos").scrollIntoView({behavior: "smooth", block: "start"}); });
    }
    for (const z of D.vazios.filter(z => z.veiculo === n)) {
      let a = z.iniMin, b = z.fimMin; if (b < a) b += 1440;
      const g = el("g", {}, svg);
      el("rect", {x: x(a), y: y + 10, width: Math.max(2, x(b) - x(a)), height: RH - 20, rx: 3, fill: cor[n], opacity: 0.32}, g);
      el("rect", {x: x(a) - 2, y: y, width: x(b) - x(a) + 4, height: RH, fill: "transparent"}, g);
      g.addEventListener("mousemove", e => {
        const tip = $("#tip");
        tip.innerHTML = `<b>${esc(n)}</b> · vazio<br>${z.ini} ${esc(ROT[z.de])} → ${z.fim} ${esc(ROT[z.para])}<br><span class="m">${nf(z.km, 1)} km sem passageiros</span>`;
        tip.hidden = false; moverTip(e);
      });
      g.addEventListener("mouseleave", () => $("#tip").hidden = true);
    }
  });
}

resumo(); montarMapa(); chips(); desenharRotas(); gantt();
</script>
</body></html>
"""


def pagina(res, M, GEO, cfg):
    d = json.dumps(dados_mapa(res, M, GEO, cfg), ensure_ascii=False).replace("</", "<\\/")
    return HTML.replace("__DADOS__", d)
