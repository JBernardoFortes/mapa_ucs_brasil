import argparse
import csv
import json
import re
import shutil
import subprocess
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import folium
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

BASE = Path(__file__).parent
UCS = BASE / "ucs_federais_protecao_integral.csv"
MUNICIPIOS = BASE / "municipios_ibge.csv"
MALHA_UFS = BASE / "malha_ufs_ibge.geojson"
MALHA_MUNICIPIOS = BASE / "malha_municipios_ibge.geojson"
PASTA_MAPAS = BASE / "mapas"

CODIGO_UF = {11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
             21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
             28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR",
             42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF"}

REGIOES = {
    "Norte": [11, 12, 13, 14, 15, 16, 17],
    "Nordeste": [21, 22, 23, 24, 25, 26, 27, 28, 29],
    "Sudeste": [31, 32, 33, 35],
    "Sul": [41, 42, 43],
    "Centro-Oeste": [50, 51, 52, 53],
}
REGIAO_POR_UF = {uf: nome for nome, ufs in REGIOES.items() for uf in ufs}

TIPOS = {
    "PARNA": ("Parque Nacional", "#1f6fb4"),
    "ESEC": ("Estação Ecológica", "#2e9e4f"),
    "REBIO": ("Reserva Biológica", "#f2c12e"),
    "REVIS": ("Refúgio de Vida Silvestre", "#d6372c"),
    "MONA": ("Monumento Natural", "#7a3fa0"),
}
INTENSIDADE = {1: 0.45, 2: 0.65, 3: 0.82, 4: 1.0}
POSICAO_ROTULOS = {"Norte": ("esquerda", 4.5), "Centro-Oeste": ("esquerda", -14), "Sul": ("esquerda", -30),
                   "Nordeste": ("direita", -4), "Sudeste": ("direita", -20)}
LON_ESQUERDA = -74.0
LON_DIREITA = -33.0
MARGEM_REGIOES = 0.15
NAVEGADORES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]

CSS = """<style>
body{background:#ffffff}
.leaflet-container{background:#ffffff !important}
.titulo{position:fixed;top:14px;left:20px;z-index:999;font-family:Georgia,serif;color:#222}
.titulo b{font-size:32px}
.titulo span{display:block;font-family:sans-serif;font-size:17px;color:#555}
.legenda{position:fixed;right:clamp(8px,1.25vw,20px);bottom:clamp(8px,1.25vw,20px);z-index:999;background:#fff;
  padding:.9em 1.1em;border-radius:10px;font-family:sans-serif;font-size:clamp(13px,1.15vw,18px);color:#222;
  box-shadow:0 0 0 1px #ddd;box-sizing:border-box;max-width:calc(100vw - 16px);max-height:60vh;overflow-y:auto}
.legenda h4{margin:0 0 .4em;font-size:1.25em}
.linha{margin:.5em 0}
.linha i{display:inline-block;width:1.3em;height:1.1em;margin-right:.4em;vertical-align:middle;box-shadow:inset 0 0 0 1px #bbb}
.legenda small{display:block;margin-top:.5em;font-size:.8em;color:#555}
.rotulo{font:bold clamp(9px,1.25vw,20px) sans-serif;color:#222;background:rgba(255,255,255,.93);border-radius:8px;
  padding:.3em .55em;box-shadow:0 2px 8px rgba(0,0,0,.25);width:max-content;max-width:clamp(120px,20vw,240px);
  white-space:normal;overflow-wrap:anywhere}
.pct{font:clamp(8px,.95vw,15px) sans-serif;font-weight:normal;color:#222;white-space:normal;overflow-wrap:anywhere}
.esq{transform:translateX(-100%)}
@media (max-width: 768px){
.titulo{top:8px;left:10px;right:10px}
.titulo b{font-size:20px}
.titulo span{font-size:13px}
.legenda{left:8px;right:8px;bottom:8px;max-width:none;max-height:45vh}
}
</style>"""


def norm(texto):
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]", " ", texto).split())


def carregar_dados():
    porUf = {}
    porNome = defaultdict(list)
    with MUNICIPIOS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            uf = CODIGO_UF[int(r["codigo_uf"])]
            porUf[(uf, norm(r["nome"]))] = r["codigo_ibge"]
            porNome[norm(r["nome"])].append(r["codigo_ibge"])

    contagem = defaultdict(Counter)
    contagemMunicipio = defaultdict(Counter)
    totalPorTipo = Counter()
    faltando = []
    with UCS.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            totalPorTipo[r["categoria"]] += 1
            ufs = [u for u in r["uf"].split("/") if u in CODIGO_UF.values()]
            regioes = set()
            for m in filter(None, r["municipios"].split(" | ")):
                chave = norm(m)
                codigo = next((porUf[(uf, chave)] for uf in ufs if (uf, chave) in porUf), None)
                if codigo is None and len(porNome[chave]) == 1:
                    codigo = porNome[chave][0]
                if codigo:
                    regioes.add(REGIAO_POR_UF[int(codigo[:2])])
                    contagemMunicipio[codigo][r["categoria"]] += 1
                else:
                    faltando.append(f"{r['nome']} -> {m} ({r['uf']})")
            for regiao in regioes:
                contagem[regiao][r["categoria"]] += 1
    return contagem, contagemMunicipio, totalPorTipo, faltando


def rotulo_quantidades(por_tipo):
    return "".join(f"<div class='pct'>{TIPOS[t][0]}: {por_tipo[t]}</div>" for t in TIPOS if por_tipo[t])


def detalhe_quantidades(por_tipo):
    return " · ".join(f"{TIPOS[t][0]}: {por_tipo[t]}" for t in TIPOS if por_tipo[t])


def mistura(hex_base, t):
    canais = [int(hex_base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(255 + (c - 255) * t):02x}" for c in canais)


def fatiar(geom, pesos):
    total = sum(n for _, n in pesos)
    minx, miny, maxx, maxy = geom.bounds
    partes = []
    acumulado = 0
    for tipo, n in pesos:
        x0 = minx + (maxx - minx) * acumulado / total
        acumulado += n
        x1 = minx + (maxx - minx) * acumulado / total
        parte = geom.intersection(box(x0, miny - 1, x1, maxy + 1))
        if not parte.is_empty and parte.geom_type in ("Polygon", "MultiPolygon"):
            partes.append((tipo, parte))
    return partes


def uniao_regioes():
    ufs = json.loads(MALHA_UFS.read_text(encoding="utf-8"))["features"]
    geoms_por_regiao = defaultdict(list)
    for feature in ufs:
        uf = int(feature["properties"]["codarea"])
        geoms_por_regiao[REGIAO_POR_UF[uf]].append(shape(feature["geometry"]))
    return {regiao: unary_union(geoms) for regiao, geoms in geoms_por_regiao.items()}


def features_regioes(uniao, contagem):
    maximo = max(sum(c.values()) for c in contagem.values())
    features = []
    for regiao, geom in uniao.items():
        por_tipo = contagem[regiao]
        total = sum(por_tipo.values())
        propriedades = {"regiao": regiao, "total": total, "detalhe": detalhe_quantidades(por_tipo)}
        t = 0.4 + 0.6 * total / maximo
        pesos = [(tipo, por_tipo[tipo]) for tipo in TIPOS if por_tipo[tipo]]
        for tipo, parte in fatiar(geom.buffer(-MARGEM_REGIOES, join_style=2), pesos):
            features.append({"type": "Feature",
                             "properties": {**propriedades, "cor": mistura(TIPOS[tipo][1], t)},
                             "geometry": mapping(parte)})
    return features


def features_municipios(contagemMunicipio):
    malha = json.loads(MALHA_MUNICIPIOS.read_text(encoding="utf-8"))["features"]
    nomes = {}
    with MUNICIPIOS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            nomes[r["codigo_ibge"]] = f"{r['nome']}/{CODIGO_UF[int(r['codigo_uf'])]}"
    features = []
    for feature in malha:
        codigo = feature["properties"]["codarea"]
        por_tipo = contagemMunicipio.get(codigo)
        propriedades = {"nome": nomes.get(codigo, "")}
        if por_tipo:
            dominante = max((t for t in TIPOS if por_tipo[t]), key=lambda t: por_tipo[t])
            propriedades["detalhe"] = detalhe_quantidades(por_tipo)
            propriedades["cor"] = TIPOS[dominante][1]
        else:
            propriedades["detalhe"] = "Sem UC federal de proteção integral"
            propriedades["cor"] = "#ffffff"
        features.append({"type": "Feature", "properties": propriedades, "geometry": feature["geometry"]})
    return features


def features_contorno(uniao):
    return [{"type": "Feature", "properties": {"regiao": regiao}, "geometry": mapping(geom)}
            for regiao, geom in uniao.items()]


def legenda_html(totalPorTipo, nota):
    linhas = []
    for chave, (nome, cor) in TIPOS.items():
        linhas.append(f"<div class='linha'><i style='background:{cor}'></i>{nome} "
                      f"({totalPorTipo[chave]} UCs)</div>")
    linhas.append(f"<small>{nota}</small>")
    return f"<div class='legenda'><h4>Tipo de UC</h4>{''.join(linhas)}</div>"


def montar_mapa(camadas, contagem, uniao, totalPorTipo, nota, titulo, arquivo):
    mapa = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles=None, zoom_snap=0,
                      zoom_control=False, attribution_control=False)
    for camada in camadas:
        camada.add_to(mapa)
    for regiao, geom in uniao.items():
        lado, lat = POSICAO_ROTULOS[regiao]
        lon = LON_ESQUERDA if lado == "esquerda" else LON_DIREITA
        ponto = geom.representative_point()
        folium.PolyLine([[ponto.y, ponto.x], [lat - 2, lon]], color="#222222", weight=1, opacity=0.6).add_to(mapa)
        classe = "rotulo esq" if lado == "esquerda" else "rotulo"
        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(html=f"<div class='{classe}'>{regiao}<br>{sum(contagem[regiao].values())} UCs"
                                f"{rotulo_quantidades(contagem[regiao])}</div>"),
        ).add_to(mapa)

    mapa_js = mapa.get_name()
    mapa.get_root().script.add_child(folium.Element(
        f"function ajustar() {{ const titulo = document.querySelector('.titulo'); "
        f"const legenda = document.querySelector('.legenda'); const mobile = window.innerWidth <= 768; "
        f"{mapa_js}.invalidateSize(); {mapa_js}.fitBounds([[-35.5, -88.0], [6.5, -20.0]], {{"
        f"paddingTopLeft: [mobile ? 10 : 40, titulo.offsetHeight + 20], "
        f"paddingBottomRight: mobile ? [10, legenda.offsetHeight + 10] : [legenda.offsetWidth + 40, 40]}}); }}"
        "window.addEventListener('load', ajustar); window.addEventListener('resize', ajustar);"
    ))
    mapa.get_root().header.add_child(folium.Element(CSS))
    mapa.get_root().html.add_child(folium.Element(
        "<div class='titulo'><b>Unidades de Conservação de proteção integral federais</b>"
        f"<span>{titulo}. Fonte: ICMBio, atributos oficiais (14/10/2022)</span></div>"
    ))
    mapa.get_root().html.add_child(folium.Element(legenda_html(totalPorTipo, nota)))
    mapa.save(arquivo)
    return arquivo


def gerar_mapa_regioes(contagem, uniao, totalPorTipo):
    camadas = [folium.GeoJson(
        {"type": "FeatureCollection", "features": features_regioes(uniao, contagem)},
        style_function=lambda f: {"fillColor": f["properties"]["cor"], "color": "#999999",
                                  "weight": 0.5, "fillOpacity": 1},
        highlight_function=lambda f: {"color": "#222222", "weight": 2},
        tooltip=folium.GeoJsonTooltip(fields=["regiao", "total", "detalhe"],
                                      aliases=["Região:", "UCs:", "Por tipo:"]),
    )]
    nota = ("Cada região tem faixas verticais proporcionais à quantidade de UCs de cada tipo.<br>"
            "Quanto mais UCs na região, mais forte a cor.")
    return montar_mapa(camadas, contagem, uniao, totalPorTipo, nota, "UCs por região e tipo",
                       PASTA_MAPAS / "mapa_ucs.html")


def gerar_mapa_municipios(contagem, contagemMunicipio, uniao, totalPorTipo):
    camadas = [
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features_municipios(contagemMunicipio)},
            style_function=lambda f: {"fillColor": f["properties"]["cor"], "color": "#d0d0d0",
                                      "weight": 0.2, "fillOpacity": 1},
            highlight_function=lambda f: {"color": "#222222", "weight": 1.5},
            tooltip=folium.GeoJsonTooltip(fields=["nome", "detalhe"], aliases=["Município:", "UCs:"]),
        ),
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features_contorno(uniao)},
            style_function=lambda f: {"fill": False, "color": "#222222", "weight": 2.5},
        ),
    ]
    nota = ("Cada município tem a cor do tipo de UC mais frequente nele.<br>"
            "As linhas escuras separam as regiões.")
    return montar_mapa(camadas, contagem, uniao, totalPorTipo, nota, "UCs por município e tipo",
                       PASTA_MAPAS / "mapa_ucs_municipios.html")


def exportar_foto(html):
    navegador = next((p for p in map(shutil.which, NAVEGADORES) if p), None)
    if navegador is None:
        raise SystemExit("Nenhum Chromium/Chrome encontrado para gerar a foto.")
    png = html.with_suffix(".png")
    subprocess.run([navegador, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=2", "--window-size=1600,1000",
                    "--virtual-time-budget=10000", f"--screenshot={png}", html.resolve().as_uri()],
                   check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return png


def main():
    parser = argparse.ArgumentParser(description="Gera os mapas de UCs por região e por município.")
    parser.add_argument("--foto", action="store_true", help="também exporta os mapas como PNG")
    args = parser.parse_args()

    contagem, contagemMunicipio, totalPorTipo, faltando = carregar_dados()
    uniao = uniao_regioes()
    PASTA_MAPAS.mkdir(exist_ok=True)

    htmls = [gerar_mapa_regioes(contagem, uniao, totalPorTipo),
             gerar_mapa_municipios(contagem, contagemMunicipio, uniao, totalPorTipo)]
    for html in htmls:
        print(html.name)
        if args.foto:
            print(f"  foto: {exportar_foto(html).name}")

    if faltando:
        print(f"Não encontrados ({len(faltando)}):")
        for linha in faltando:
            print(f"  {linha}")


if __name__ == "__main__":
    main()
