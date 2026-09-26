"""
Cálculo de HP efetivo (vida efetiva) contra um atacante, com gráficos para o Discord.

Fórmula baseada nos testes empíricos feitos pelo @gpw:
https://docs.google.com/spreadsheets/d/14DlYeauGCtIw-6FuJ2LVNLPA-hKpEOTseOvL3b6aKh4
"""

import io

import numpy as np
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

# Constantes para os cálculos
DANO_MINIMO = 0.05      # 5% do AP sempre passa qualquer DR e Evasão, para prevenir invulnerabilidade.
MISS_BASE = 0.33        # % de erro quando precisão = evasão.
MISS_PONTO = 0.0025     # Cada ponto de diferença entre evasão e precisão vale 0,25% de chance de erro.
MISS_MAX = 0.9          # A chance de erro tem um máximo de 90%.

PONTOS_TESTE = 10       # Quantos pontos de DR/Evasão simulamos para dizer o que vale mais a pena subir

# Zonas dos gráficos
ZONA_NADA = "nada"          # subir não muda nada
ZONA_BOA = "boa"            # subir ajuda bem
ZONA_MENOS = "menos"        # subir ajuda, mas cada ponto rende menos

CORES = {
    "fundo": "#2b2d31",
    "painel": "#313338",
    "texto": "#f2f3f5",
    "texto_suave": "#b5bac1",
    "grade": "#4e5058",
    "curva_dr": "#5b9cf5",
    "curva_evasao": "#3fc488",
    "voce": "#ff7a3d",
    "vida_real": "#f2f3f5",
    ZONA_BOA: "#3fc488",
    ZONA_MENOS: "#f0b232",
    ZONA_NADA: "#80848e",
}

TEXTO_ZONA = {
    ZONA_BOA: "SUBIR AJUDA",
    ZONA_MENOS: "AJUDA MENOS",
    ZONA_NADA: "NÃO AJUDA NADA",
}


def calculo_miss(precisao, evasao):
    """Chance de o atacante errar, baseada na precisão dele e na sua evasão."""
    return min(max(MISS_BASE + MISS_PONTO * (evasao - precisao), 0), MISS_MAX)


def hpe(vida, ap, miss, dr, drp):
    """HP efetivo: quanto de dano 'sem defesa' seria preciso para te matar."""
    dano_acerto = max(ap - dr, DANO_MINIMO * ap)                    # Um acerto interage apenas com a DR.
    dano_erro = max(dano_acerto * (1 - miss), DANO_MINIMO * ap)     # Um erro pega o dano do acerto e diminui pela chance de errar.
    # Média ponderada de acertos e erros, dividida pelo AP (quanto do AP passa), e por fim o DRP (redutor global)
    f = ((1 - miss) * dano_acerto + miss * dano_erro) / ap * (1 - drp)
    return vida / f


def formatar_numero(valor):
    """1234567 -> '1.234.567'"""
    return f"{int(round(valor)):,}".replace(",", ".")


def formatar_decimal(valor, casas=1):
    """3.25 -> '3,3'"""
    return f"{valor:.{casas}f}".replace(".", ",")


def _zona_em(valor, zonas):
    for inicio, fim, zona in zonas:
        if inicio <= valor < fim:
            return zona
    return zonas[-1][2]


def calcular(vida, dr, evasao, drp, ap, precisao):
    """
    Faz todas as contas e devolve um dicionário com os números prontos para o embed e o gráfico.
    drp vem como fração (0.3 = 30%).
    """
    miss = calculo_miss(precisao, evasao)
    hp_efetivo = hpe(vida, ap, miss, dr, drp)

    # DR: a partir do "check" o golpe já está no dano mínimo, subir DR não muda mais nada
    dr_check = round(ap * (1 - DANO_MINIMO))
    # DR: a partir do "cotovelo" o golpe errado já está no dano mínimo, cada ponto rende menos
    dr_cotovelo = min(round(ap * (1 - DANO_MINIMO / (1 - miss))), dr_check)

    # Evasão: abaixo do início o inimigo nunca erra; acima do check ele já erra o máximo (90%)
    evasao_inicio = round(precisao - MISS_BASE / MISS_PONTO)
    evasao_check = round(precisao + (MISS_MAX - MISS_BASE) / MISS_PONTO)
    # Se até o golpe acertado já está no dano mínimo, errar ou acertar dá no mesmo: evasão não ajuda
    evasao_inutil = (ap - dr) <= DANO_MINIMO * ap
    if evasao_inutil:
        evasao_cotovelo = evasao_inicio
    else:
        miss_cotovelo = min(1 - DANO_MINIMO / (1 - dr / ap), MISS_MAX)
        evasao_cotovelo = round(precisao + (miss_cotovelo - MISS_BASE) / MISS_PONTO)
        evasao_cotovelo = min(max(evasao_cotovelo, evasao_inicio), evasao_check)

    # Curvas
    dr_max = max(ap, dr, dr_check) * 1.05
    dr_range = np.arange(0, dr_max, 1)
    hpe_dr = np.array([hpe(vida, ap, miss, x, drp) for x in dr_range])

    evasao_max = max(precisao, evasao, evasao_check) * 1.05
    evasao_range = np.arange(0, evasao_max, 1)
    hpe_evasao = np.array([hpe(vida, ap, calculo_miss(precisao, x), dr, drp) for x in evasao_range])

    zonas_dr = [
        (0, dr_cotovelo, ZONA_BOA),
        (dr_cotovelo, dr_check, ZONA_MENOS),
        (dr_check, dr_max, ZONA_NADA),
    ]
    if evasao_inutil:
        zonas_evasao = [(0, evasao_max, ZONA_NADA)]
    else:
        zonas_evasao = [
            (0, max(evasao_inicio, 0), ZONA_NADA),
            (max(evasao_inicio, 0), evasao_cotovelo, ZONA_BOA),
            (evasao_cotovelo, evasao_check, ZONA_MENOS),
            (evasao_check, evasao_max, ZONA_NADA),
        ]
    zonas_dr = [z for z in zonas_dr if z[1] > z[0]]
    zonas_evasao = [z for z in zonas_evasao if z[1] > z[0]]

    # Quanto a vida efetiva sobe se ganhar alguns pontos de cada atributo
    hpe_mais_dr = hpe(vida, ap, miss, dr + PONTOS_TESTE, drp)
    hpe_mais_evasao = hpe(vida, ap, calculo_miss(precisao, evasao + PONTOS_TESTE), dr, drp)
    ganho_dr = (hpe_mais_dr / hp_efetivo - 1) * 100
    ganho_evasao = (hpe_mais_evasao / hp_efetivo - 1) * 100

    return {
        "vida": vida, "dr": dr, "evasao": evasao, "drp": drp, "ap": ap, "precisao": precisao,
        "miss": miss,
        "hp_efetivo": hp_efetivo,
        "multiplicador": hp_efetivo / vida,
        "multiplicador_max": 1 / (DANO_MINIMO * (1 - drp)),
        "dr_check": dr_check, "dr_cotovelo": dr_cotovelo,
        "evasao_inicio": evasao_inicio, "evasao_cotovelo": evasao_cotovelo, "evasao_check": evasao_check,
        "evasao_inutil": evasao_inutil,
        "dr_range": dr_range, "hpe_dr": hpe_dr,
        "evasao_range": evasao_range, "hpe_evasao": hpe_evasao,
        "zonas_dr": zonas_dr, "zonas_evasao": zonas_evasao,
        "zona_dr_atual": _zona_em(dr, zonas_dr),
        "zona_evasao_atual": _zona_em(evasao, zonas_evasao),
        "ganho_dr": ganho_dr, "ganho_evasao": ganho_evasao,
    }


def recomendacao(res):
    """Frase curta dizendo o que vale mais a pena subir."""
    ganho_dr, ganho_evasao = res["ganho_dr"], res["ganho_evasao"]
    if ganho_dr < 0.05 and ganho_evasao < 0.05:
        return "NENHUM DOS DOIS: contra esse inimigo você já está no máximo de DR e Evasão."
    if ganho_dr >= ganho_evasao * 1.2:
        return "SUBA DR. Contra esse inimigo ela rende mais que Evasão."
    if ganho_evasao >= ganho_dr * 1.2:
        return "SUBA EVASÃO. Contra esse inimigo ela rende mais que DR."
    return "TANTO FAZ: DR e Evasão rendem quase igual contra esse inimigo."


def _desenhar_painel(ax, titulo, nome_x, xs, ys, zonas, x_atual, y_atual, vida, cor_curva, rotulo_atual):
    ax.set_facecolor(CORES["painel"])
    x_max = xs[-1]

    # Faixas coloridas de fundo + nome da zona
    y_topo = max(ys.max(), y_atual) * 1.18
    for inicio, fim, zona in zonas:
        ax.axvspan(inicio, fim, color=CORES[zona], alpha=0.16, linewidth=0)
        if (fim - inicio) / x_max >= 0.12:
            ax.text((inicio + fim) / 2, y_topo * 0.97, TEXTO_ZONA[zona],
                    ha="center", va="top", fontsize=11, fontweight="bold", color=CORES[zona])

    # Vida real (sem defesa) como referência
    ax.axhline(vida, color=CORES["vida_real"], linestyle="--", linewidth=1.2, alpha=0.6)
    ax.text(x_max * 0.99, vida, f"sua vida de verdade: {formatar_numero(vida)}",
            va="bottom", ha="right", fontsize=10, color=CORES["texto_suave"],
            bbox=dict(boxstyle="round,pad=0.2", fc=CORES["painel"], ec="none", alpha=0.85))

    # Curva
    ax.plot(xs, ys, color=cor_curva, linewidth=3.5)

    # Você está aqui
    ax.scatter([x_atual], [y_atual], s=260, color=CORES["voce"], zorder=5,
               edgecolors="white", linewidths=2)
    fracao_x = x_atual / x_max if x_max else 0
    deslocamento_x = -40 if fracao_x > 0.6 else 40
    # Se a bolinha está no alto do gráfico, o balão vai para baixo dela
    deslocamento_y = -75 if y_atual / y_topo > 0.5 else 55
    ax.annotate(
        f"VOCÊ ESTÁ AQUI\n{rotulo_atual}",
        xy=(x_atual, y_atual),
        xytext=(deslocamento_x, deslocamento_y), textcoords="offset points",
        ha="right" if deslocamento_x < 0 else "left", va="bottom" if deslocamento_y > 0 else "top",
        fontsize=12, fontweight="bold", color=CORES["texto"],
        bbox=dict(boxstyle="round,pad=0.5", fc=CORES["voce"], ec="white", lw=1.5),
        arrowprops=dict(arrowstyle="-|>", color="white", lw=2),
        zorder=6,
    )

    ax.set_title(titulo, fontsize=16, fontweight="bold", color=CORES["texto"], loc="left", pad=12)
    ax.set_xlabel(nome_x, fontsize=12, color=CORES["texto"])
    ax.set_ylabel("Vida efetiva", fontsize=12, color=CORES["texto"])
    ax.set_xlim(0, x_max)
    ax.set_ylim(0, y_topo)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: formatar_numero(v)))
    ax.tick_params(colors=CORES["texto_suave"], labelsize=10)
    for borda in ax.spines.values():
        borda.set_color(CORES["grade"])
    ax.grid(color=CORES["grade"], alpha=0.5, linewidth=0.8)


def gerar_grafico(res):
    """Gera a imagem PNG com os dois gráficos e devolve um BytesIO pronto para discord.File."""
    fig = Figure(figsize=(11, 12.5), dpi=100, facecolor=CORES["fundo"])
    ax_dr, ax_evasao = fig.subplots(2, 1)

    hp_txt = formatar_numero(res["hp_efetivo"])
    fig.suptitle(
        f"Sua vida efetiva: {hp_txt}   (sua vida real: {formatar_numero(res['vida'])})\n"
        f"Contra um inimigo com AP {res['ap']} e Precisão {res['precisao']}",
        fontsize=17, fontweight="bold", color=CORES["texto"], y=0.985,
    )

    _desenhar_painel(
        ax_dr, "1) E SE EU MUDAR MINHA DR?  (o resto fica igual)", "Sua DR",
        res["dr_range"], res["hpe_dr"], res["zonas_dr"],
        res["dr"], res["hp_efetivo"], res["vida"], CORES["curva_dr"],
        f"DR {res['dr']} = {hp_txt} de vida",
    )
    _desenhar_painel(
        ax_evasao, "2) E SE EU MUDAR MINHA EVASÃO?  (o resto fica igual)", "Sua Evasão",
        res["evasao_range"], res["hpe_evasao"], res["zonas_evasao"],
        res["evasao"], res["hp_efetivo"], res["vida"], CORES["curva_evasao"],
        f"Evasão {res['evasao']} = {hp_txt} de vida",
    )

    fig.text(
        0.5, 0.012,
        "Linha mais alta = você aguenta mais.   Bolinha laranja = você.\n"
        f"RESPOSTA: {recomendacao(res)}",
        ha="center", va="bottom", fontsize=13, fontweight="bold", color=CORES["texto"],
    )
    fig.subplots_adjust(left=0.1, right=0.97, top=0.9, bottom=0.1, hspace=0.35)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor())
    buffer.seek(0)
    return buffer
