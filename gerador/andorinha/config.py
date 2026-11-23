"""Tudo o que muda o resultado de uma geração, num lugar só."""
from __future__ import annotations

import os
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
REPO = GENERATOR_DIR.parent
DATA = GENERATOR_DIR / "data"
RAW = DATA / "raw"        # extrato do OpenStreetMap
BUILD = DATA / "build"    # tiles soltos e bancos auxiliares do Valhalla
DIST = DATA / "dist"      # pacotes .tar.zst e o catalogo.json desta geração

# Versão do Valhalla que gera os tiles. TEM de ser a mesma do motor que vai lê-los: o formato do tile muda entre
# versões (o cabeçalho mudou na 3.8). 3.9.1 é a embutida no valhalla-mobile 0.6.4 (2026-10-06), a biblioteca
# Android/iOS; até a troca, era a 3.6.3, a do valhalla-mobile 0.6.3.
# Trocar aqui = gerar tudo de novo e publicar como geração nova, com a versão nova no catálogo.
VALHALLA_VERSION = "3.9.1"

# Quem lê estes tiles, para o bloco `compatibility.andorinha` do contrato de catálogo (docs/CATALOGO.md). Mudam junto
# com VALHALLA_VERSION, e os testes (tests/test_catalog_contract.py) não deixam um ir sem o outro.
# - VALHALLA_COMMIT: o commit da tag 3.9.1 em github.com/valhalla/valhalla (conferido em 2026-11-11 pela API do GitHub:
#   refs/tags/3.9.1 -> bafb699...).
# - TILE_FORMAT_VERSION: o texto que o Valhalla grava no cabeçalho de cada tile (deslocamento 0x10, 16 bytes) e que o
#   motor confere ao abrir: é a versão do Valhalla que gerou o tile ("3.9.1" em 0/002/012.gph da geração
#   2026-11-09-c17dff60).
# - VALHALLA_MOBILE_*: a biblioteca Android/iOS que embute esse mesmo commit. Na tag 0.6.4 de
#   github.com/Rallista/valhalla-mobile, o submódulo src/valhalla aponta para bafb699... (conferido em 2026-11-11). O
#   sha256 é o do AAR do Maven Central, baixado em 2026-11-11; as ABIs são as pastas jni/ dele.
VALHALLA_COMMIT = "bafb69902220615a48307d5c790fb6c943802ba4"
TILE_FORMAT_VERSION = VALHALLA_VERSION
VALHALLA_MOBILE_VERSION = "0.6.4"
VALHALLA_MOBILE_ARTIFACT = "io.github.rallista:valhalla-mobile:" + VALHALLA_MOBILE_VERSION
VALHALLA_MOBILE_AAR_SHA256 = "5c3118b51ff4b17655d6239a5e3f2ebdb0c36c0da3ba34a771bd7078f247dd00"
VALHALLA_MOBILE_ABIS = ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")

# Extrato do Brasil, de uma destas fontes, na ordem de OSM_SOURCES: a primeira que entregar um extrato conferido pelo
# md5 vale; se uma falha (fora do ar, tempo esgotado, md5 que não confere), a geração tenta a seguinte e avisa no log.
# As duas recortam o país inteiro, ilhas oceânicas inclusive (conferido contra a malha do IBGE em 2026-11-08); a
# divisão por UF vem da malha, não do recorte da fonte. ANDORINHA_OSM_SOURCES muda a ordem (ex.: "geofabrik").
OSM_SOURCES = tuple(s.strip() for s in (os.environ.get("ANDORINHA_OSM_SOURCES") or "openstreetmap.fr,geofabrik")
                    .split(",") if s.strip())
# openstreetmap.fr: o extrato de sempre (~2,35 GB); a data e o md5 ficam ao lado.
OSM_PBF_URL = "https://download.openstreetmap.fr/extracts/south-america/brazil-latest.osm.pbf"
OSM_STATE_URL = "https://download.openstreetmap.fr/extracts/south-america/brazil.state.txt"
OSM_MD5_URL = "https://download.openstreetmap.fr/extracts/south-america/brazil.osm.pbf.md5"
# Geofabrik (~2,1 GB, sem os metadados de usuário, que o Valhalla não usa): o arquivo datado do dia
# (brazil-AAMMDD.osm.pbf) tem o próprio .md5 e não muda depois de publicado. Desde 2026-11-22 o `-latest` dá dois saltos
# (south-america/brazil-latest -> 301 -> south-america/brazil/brazil-latest -> 307 -> brazil/brazil-AAMMDD) e o datado do
# dia fica na listagem horas antes de responder (404, ou 502 no .md5), então o gerador também lê a listagem da pasta e
# cai para o datado do dia anterior. A data e a hora dos dados vêm do cabeçalho do próprio arquivo (andorinha/pbf_header.py).
GEOFABRIK_LATEST_URL = "https://download.geofabrik.de/south-america/brazil/brazil-latest.osm.pbf"
GEOFABRIK_LISTING_URL = "https://download.geofabrik.de/south-america/brazil/"
# Quantos datados da listagem (do mais novo ao mais velho) a geração tenta depois do que o `-latest` aponta.
GEOFABRIK_LISTED_DATES = 3

# Malha oficial das UFs (API de malhas do IBGE, v3, qualidade máxima): decide em que pacote cada tile entra. A geração
# usa só a cópia no repositório, baixada desta URL em 2026-11-08 (1.001.279 bytes, as 27 UFs, idêntica à de 2026-09-30,
# que gerou as primeiras releases). Até 2026-11-08 a geração baixava a malha; desde 2026-11-03 a API não respondia aos
# executores do GitHub Actions. Para trocar a cópia (uma divisa mudou): baixar desta URL, conferir com
# `sources.mesh_problem` e atualizar o sha256; os pacotes da geração seguinte podem mudar de composição.
IBGE_MESH_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
                 "?formato=application/vnd.geo%2Bjson&intrarregiao=UF&qualidade=maxima")
IBGE_MESH_COPY = GENERATOR_DIR / "ibge_malha_uf.json"
IBGE_MESH_COPY_SHA256 = "4435b2e0e62de6d68f5c5387e01efc31655242948ae9434b78a3b5b05582e497"
IBGE_MESH_COPY_DATE = "2026-11-08"  # o dia em que a cópia foi baixada (vai para `sources` no catálogo)

# Malha municipal (a mesma API, qualidade intermediária): o contorno de cada município, para os arquivos de apoio por
# UF (andorinha/municipalities.py). Não decide em que pacote um tile entra. A geração usa só a cópia no repositório
# (gzip -9 -n), baixada desta URL em 2026-11-14: 12.307.459 bytes abertos, 5.570 municípios, código em `codarea`. O
# sha256 é o do conteúdo descompactado, o que a API devolveu. Para trocar a cópia (município novo, limite mudado):
# baixar desta URL, conferir com `municipalities.mesh_problem`, gravar com `gzip -9 -n` e atualizar o sha256, a data e,
# se mudou, a contagem; um município novo fica sem peso até a tabela abaixo ser refeita.
IBGE_MUNICIPAL_MESH_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
                           "?formato=application/vnd.geo%2Bjson&intrarregiao=municipio&qualidade=intermediaria")
IBGE_MUNICIPAL_MESH_COPY = GENERATOR_DIR / "ibge_malha_municipios.json.gz"
IBGE_MUNICIPAL_MESH_COPY_SHA256 = "4bae3d8d9c8dd5ec520c94a35dadf0d273f5f661a63cf0b5e95e9efdb3047588"
IBGE_MUNICIPAL_MESH_COPY_DATE = "2026-11-14"
MUNICIPALITY_COUNT = 5570

# Pesos do perfil de moto por município, para a viagem que começa e termina no mesmo município: valem para o costing
# `motorcycle` com `use_tolls` 1,0. O estudo é do próprio gerador, em lotes semanais (andorinha/weights_study.py,
# andorinha/weights_tables.py, scripts/weights.py; ver WEIGHTS_BATCH abaixo). O método, resumido (inteiro no README do
# gerador): em cada município, 32 origens x 32 destinos sorteados a até 150 m de uma via; a grade use_highways 0,2 a 0,8
# x use_trails 0,2 a MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS e depois as vizinhas a 0,1 do melhor; vence o menor tempo médio
# (empate: a menor distância média, depois a mais perto de 0,6/0,4).
# Cada linha diz com o que AQUELE município foi calculado (`geracao_tiles`, `versao_valhalla`, `data_calculo`): o
# catálogo diz, por UF, de que gerações vieram os pesos. A tabela de que cada geração partiu sobe na release dela; o
# estudo, que roda DEPOIS da publicação, sobe o resultado como assets extras da mesma release (`.proximo`), e a geração
# seguinte parte deles (senão, das tabelas normais, conferidas pelo sha256 e pela contagem do catálogo publicado): os
# pesos refeitos chegam ao app uma geração depois. As cópias abaixo, no repositório, são
# a SEMENTE: a tabela de 2026-11-14 (estudo feito fora do gerador, ~7 h numa máquina de 12 núcleos), de onde a primeira
# geração parte e a que vale quando a release anterior não tem tabela. Têm sha256 e contagem fixos aqui; trocar a semente
# = trocar o arquivo e esses dois valores (gerador/PENDENCIAS.md, "Atualizar a tabela de pesos"). Pesos de outra geração
# de tiles não param a geração: ficam ditos no catálogo e no geracao.json. Os municípios sem amostra ficam fora da
# tabela, e o leitor usa o peso padrão dele.
MOTORCYCLE_WEIGHTS_COPY = GENERATOR_DIR / "pesos_moto_municipios.csv"
MOTORCYCLE_WEIGHTS_SHA256 = "66cc4078eef4f9fee1bce2ef3074aebb79e97765cc2524db6c77b5a1984be434"
MOTORCYCLE_WEIGHTS_ROWS = 5564
MOTORCYCLE_WEIGHTS_COSTING = "motorcycle"
# Pesos do mesmo perfil por estado (`codigo_ibge;uf;use_highways;use_trails` e as mesmas três colunas de origem), para
# a viagem com todos os pontos no mesmo estado, mas em municípios diferentes; entre estados, o leitor usa o padrão
# dele. Outro estudo, com o mesmo costing, o mesmo `use_tolls` e o mesmo teto de use_trails: por UF, pelo menos 10.000
# viagens com origem e destino uniformes no polígono da UF, a até 150 m de uma via, a mesma grade e o mesmo refino
# (empate: fica o centro do refino). Uma linha por UF; o gerador refaz um estado por geração. A cópia é a semente, como
# a dos municípios.
MOTORCYCLE_STATE_WEIGHTS_COPY = GENERATOR_DIR / "pesos_moto_estados.csv"
MOTORCYCLE_STATE_WEIGHTS_SHA256 = "fd306ff04a7b0d67d72ca693ae0c06523ce015e60baf8db7a75e121095dbe685"
MOTORCYCLE_STATE_WEIGHTS_ROWS = 27
MOTORCYCLE_WEIGHTS_USE_TOLLS = 1.0
MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS = 0.4

# O estudo dos pesos em lotes (andorinha/weights_study.py, andorinha/weights_tables.py, scripts/weights.py): a cada
# geração, num job à parte do workflow que roda depois da publicação, o gerador refaz os pesos dos WEIGHTS_BATCH municípios de linha mais velha e de
# WEIGHTS_STATES estado(s), sobre os tiles da própria geração. Com 5.570 municípios, 600 por geração dão a volta em 10
# semanas. O lote cabe no limite de 6 h do job com folga (medições e estimativa em gerador/README.md).
# - ANDORINHA_WEIGHTS_BATCH e ANDORINHA_WEIGHTS_STATES mudam os tamanhos (0 desliga o estudo dos municípios ou dos
#   estados);
# - ANDORINHA_WEIGHTS_MINUTES é o orçamento de tempo do estudo: o lote dos municípios pára de começar municípios novos
#   aos 70% dele e o estado tem até o fim; o que não deu tempo fica para a geração seguinte (a rotação pega os de linha
#   mais velha);
# - ANDORINHA_WEIGHTS_WORKERS: processos do estudo (padrão: um por núcleo).
WEIGHTS_BATCH = int(os.environ.get("ANDORINHA_WEIGHTS_BATCH") or 600)
WEIGHTS_STATES = int(os.environ.get("ANDORINHA_WEIGHTS_STATES") or 1)
WEIGHTS_MINUTES = float(os.environ.get("ANDORINHA_WEIGHTS_MINUTES") or 300)
WEIGHTS_WORKERS = int(os.environ.get("ANDORINHA_WEIGHTS_WORKERS") or os.cpu_count() or 1)

USER_AGENT = "andorinha-gerador/1 (+https://github.com/Murkiriel/andorinha)"

# Threads do valhalla_build_tiles: as da máquina, no máximo 8 (ANDORINHA_THREADS muda). Com todas as 16 de uma
# máquina Windows ele caiu duas vezes sem log (0xC0000409), no Brasil e no DF; com 8 terminou nas duas. O Valhalla 3.9
# limitou uma ordenação interna a 8. Menos threads também baixam o pico de memória (ver gerador/README.md).
THREADS = int(os.environ.get("ANDORINHA_THREADS") or min(8, os.cpu_count() or 1))

# Margem, em graus (~5 km), em volta do polígono da UF ao escolher os tiles dela: pega pontes, balsas e vias que
# cruzam a divisa. Tile na margem de duas UFs entra nas duas.
STATE_MARGIN_DEG = 0.05

# Espaço livre exigido na unidade de data/ antes do build. Medido: extrato (2,3 GB) + intermediários do Valhalla
# (~12 GB, apagados no fim) + tiles (3,2 GB) + pacotes (1,5 GB), com folga. Sem isso, o build cai depois de 15 min.
# ANDORINHA_MIN_FREE_DISK_GB muda (ex.: numa máquina do GitHub Actions depois de liberar espaço).
MIN_FREE_DISK_BYTES = int(float(os.environ.get("ANDORINHA_MIN_FREE_DISK_GB") or 25) * 1_000_000_000)

# Quantas gerações ficam nas releases: a do catálogo publicado e as anteriores mais recentes. As demais são apagadas
# no fim de cada publicação (publishing.prune_releases). 0 = não apaga nenhuma. ANDORINHA_KEEP_GENERATIONS muda.
# 12 gerações semanais (~3 meses, ~16 GB): quem instalou uma geração acrescenta outro estado dela enquanto a release
# existir; depois, precisa atualizar tudo. Eram 3 enquanto a geração era a cada 29 dias (~87 dias).
KEEP_GENERATIONS = int(os.environ.get("ANDORINHA_KEEP_GENERATIONS") or 12)

# O GitHub Releases recusa arquivo a partir de 2 GiB; esta é a trava, com folga.
MAX_ASSET_BYTES = 1_900_000_000
# Nível do .tar.zst de cada pacote. O 19 com a janela padrão (8 MB) dá ~15% menos que o .tar.gz; níveis maiores
# e janelas maiores quase não ganham (medido em 2026-10-05) e pedem mais memória para descompactar no celular.
ZSTD_LEVEL = 19
# Fusos horários dos tiles (andorinha/timezones.py): release fixada do timezone-boundary-builder, a fonte do
# valhalla_build_timezones. Trocar de release muda os tiles só onde uma fronteira de fuso mudou. O zip é conferido
# pelo sha256 antes do uso: 55.874.703 bytes, baixado em 2026-11-11; o mesmo sha256 que o GitHub mostra no `digest`
# do arquivo da release (publicada em 2026-09-16). Para trocar de release: mudar as duas linhas juntas, com o sha256
# do arquivo novo, conferido também no `digest` (gh api repos/evansiroky/timezone-boundary-builder/releases/tags/<release>).
TIMEZONE_RELEASE = "2026d"
TIMEZONE_SHA256 = "e391c92c7b90339c3afe8558082b4532b8d9afc9daad68da31f4569a1ca61034"
TIMEZONE_URL = ("https://github.com/evansiroky/timezone-boundary-builder/releases/download/{release}/"
                "timezones-with-oceans.geojson.zip")

# O repositório das releases: ANDORINHA_REPO, senão o do GitHub Actions (GITHUB_REPOSITORY, que ele define sozinho),
# senão o público. Assim um repositório de teste publica e aponta os links para ele mesmo.
GITHUB_REPO = os.environ.get("ANDORINHA_REPO") or os.environ.get("GITHUB_REPOSITORY") or "Murkiriel/andorinha"
RELEASE_URL = "https://github.com/" + GITHUB_REPO + "/releases/download/{tag}/"

ATTRIBUTION = "© colaboradores do OpenStreetMap (ODbL 1.0); limites das UFs e dos municípios: IBGE"
# As licenças em SPDX, para o contrato de catálogo: os pacotes (LICENSE da raiz) e o código (gerador/LICENSE).
DATA_LICENSE = "ODbL-1.0"
CODE_LICENSE = "MIT"
REPOSITORY_URL = "https://github.com/" + GITHUB_REPO

# Marca que o gerador cria em DIST ao começar e apaga ao terminar: se ela existe, a geração caiu no meio.
IN_PROGRESS_MARKER = "GERACAO_EM_ANDAMENTO"

STATES = {"AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia", "CE": "Ceará",
          "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MG": "Minas Gerais",
          "MS": "Mato Grosso do Sul", "MT": "Mato Grosso", "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco",
          "PI": "Piauí", "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia",
          "RR": "Roraima", "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo",
          "TO": "Tocantins"}

# Código IBGE da UF (campo `codarea` da malha) -> sigla.
IBGE_STATE_CODES = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA",
                    "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
                    "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
                    "51": "MT", "52": "GO", "53": "DF"}
