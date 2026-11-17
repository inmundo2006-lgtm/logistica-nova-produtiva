# Logística de Fretamento – Usina Nova Produtiva

App Streamlit para gerir o fretamento de colaboradores: o cadastro (colaboradores, frotas e turnos) é o banco de
dados, e a logística (rotas, veículos, horários) é calculada a partir dele.

## O que tem
| Aba | O que faz |
|---|---|
| 📊 Painel | veículos no total, % de ocupação (pessoas ÷ lugares), viagens/dia, km total/dia, km vazio/dia; veículo por veículo; ocupação de cada ida e volta; frota necessária × cadastrada; botão **Recalcular a logística** |
| 🗺️ Mapa das rotas | mapa das rotas em HTML; uma linha por turno com a ida e a volta separadas; o dia de cada veículo |
| 🔁 Ida e volta por turno | cada turno com a ida de um lado e a volta do outro; opção de ver os nomes em cada veículo; Excel da escala |
| 👥 Colaboradores | cadastrar, alterar, excluir: turno, cidade/localidade, meio de transporte. Só quem está como **Fretado** entra nas rotas |
| 🚌 Frotas | cadastro dos veículos (vans, micro-ônibus, ônibus...): tipo, lugares, prestador, frota industrial/agrícola |
| ⚙️ Turnos e veículos | horários dos turnos, lotação por tipo de veículo, regras das viagens (só admin) |

O cálculo usa **frota separada**: industrial e agrícola com veículos próprios.

## Como o resultado acompanha o cadastro
O cálculo leva alguns minutos, então não roda a cada clique. Quando alguém muda turno, localidade ou transporte
de um colaborador (ou os turnos/veículos), o app avisa que **o cadastro mudou depois do último cálculo** e o admin
clica em **Recalcular a logística**. O resultado fica gravado no banco e todo mundo vê o mesmo.

## Arquivos
| Arquivo | O que é |
|---|---|
| `app.py` | telas |
| `motor.py` | cálculo das rotas (HiGHS via PuLP 2.9; manter `pulp==2.9.0`) |
| `mapa.py` | página HTML do mapa (não leva nomes de colaboradores) |
| `banco.py` | SharePoint (Graph) ou arquivos locais |
| `dados/matriz.json`, `dados/geometrias.json` | distâncias, tempos e traçados por estrada entre as localidades |
| `importar_inicial.py` | carga inicial dos colaboradores a partir de uma planilha |
| `criar_listas.py` | cria as 3 listas no SharePoint |
| `secrets_exemplo.toml` | modelo dos Secrets |

## Rodar no PC (modo local, sem SharePoint)
```
pip install -r requirements.txt
streamlit run app.py
```
Com `MODO_LOCAL = true` em `.streamlit\secrets.toml` o app entra sem login e grava em `dados_locais\`.

## Publicar (Streamlit Cloud + SharePoint)
1. Criar no site do SharePoint as 3 listas, com colunas de **texto de uma linha** (menos onde indicado):
   - **RotasFuncionarios**: Título (= cadastro), Nome, Funcao, Setor, CentroCusto, Turno, Localidade, Transporte, Observacao
   - **RotasFrotas**: Título (= placa/nº), Tipo, Lugares (número), Prestador, Frota, Situacao, Observacao
   - **RotasConfig**: Título, Valor (**várias linhas de texto, texto sem formatação**)
2. Subir esta pasta `app` para um repositório privado no GitHub (o `.gitignore` já deixa de fora segredos e dados com nomes).
3. No Streamlit Cloud: New app → `app.py` → colar os Secrets (`secrets_exemplo.toml`).
4. Carga inicial: com as credenciais no `.streamlit\secrets.toml` do PC, `python importar_inicial.py <planilha.xlsx> --gravar`.

## Limites conhecidos
- Localidade nova precisa das distâncias por estrada (`dados/matriz.json`); hoje são 9 + a usina.
- O cálculo considera que ao meio-dia não há viagem (é onde o dia dos veículos "fecha").
- Lotação com todos presentes (sem descontar folgas); o veículo espera na cidade entre uma turma e outra.
