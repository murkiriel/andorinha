# Andorinha — tiles de roteamento do Valhalla para o Brasil

**Rotas offline no Brasil com o [Valhalla](https://github.com/valhalla/valhalla)**, o motor de roteamento open
source: os tiles do grafo de vias do país inteiro, gerados a partir do OpenStreetMap e divididos em um **pacote
base** (as rodovias do país) e **um pacote por estado**. Baixe só o que precisa, extraia numa pasta e aponte o
Valhalla para ela: carro, moto, bicicleta e a pé, com instruções em português, sem internet.

O Valhalla não publica tiles prontos, e gerar os do Brasil pede até ~20 GB de RAM. O Andorinha gera e publica o
resultado.

> **Aviso.** Os dados vêm do OpenStreetMap e podem estar incompletos ou errados. A sinalização da via sempre
> prevalece.

**Índice:** [`catalogo.json`](catalogo.json) · **Pacotes:** [releases](https://github.com/Murkiriel/andorinha/releases)
(a etiqueta de cada uma é o `build_id` da geração)

## Situação

Em 2026-11-11:

| Item | Como está |
|---|---|
| Geração publicada | `2026-11-09-c17dff60`: OpenStreetMap de 2026-11-09, Valhalla 3.9.1. A mais recente está sempre em [Baixar e usar](#baixar-e-usar) e no `built_at` do [`catalogo.json`](catalogo.json) |
| Próxima geração | Domingo, 2026-11-15: uma por semana ([Geração e reprodutibilidade](#geração-e-reprodutibilidade)) |
| Pacotes | 29 arquivos por geração: a base, os 27 estados e o `LICENCA-DADOS.txt`; cerca de 1,4 GB ao todo, o maior (SP) 198 MB. A partir da próxima geração, também o `catalogo.json` dela |
| Gerações no ar | As 12 mais recentes (cerca de 3 meses); hoje são 5 |
| Formato | `.tar.zst` com os tiles do Valhalla 3.9.1; catálogo no `schema` 1 |
| Repositório | Público |

## Baixar e usar

### Baixar por estado

<!-- downloads:start -->
Geração **2026-11-09-c17dff60**: OpenStreetMap de 2026-11-09, Valhalla 3.9.1. Baixe sempre a base e os estados por onde vai rodar; confira o sha256 pelo [`catalogo.json`](catalogo.json).

| Pacote | Arquivo | Tamanho |
|---|---|---:|
| **Base** (rodovias do Brasil, sempre necessária) | [andorinha-base.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-base.tar.zst) | 52,8 MB |
| Acre (AC) | [andorinha-AC.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-AC.tar.zst) | 4,1 MB |
| Alagoas (AL) | [andorinha-AL.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-AL.tar.zst) | 20,8 MB |
| Amapá (AP) | [andorinha-AP.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-AP.tar.zst) | 2,1 MB |
| Amazonas (AM) | [andorinha-AM.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-AM.tar.zst) | 11,1 MB |
| Bahia (BA) | [andorinha-BA.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-BA.tar.zst) | 104,7 MB |
| Ceará (CE) | [andorinha-CE.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-CE.tar.zst) | 58,1 MB |
| Distrito Federal (DF) | [andorinha-DF.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-DF.tar.zst) | 17,1 MB |
| Espírito Santo (ES) | [andorinha-ES.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-ES.tar.zst) | 32,2 MB |
| Goiás (GO) | [andorinha-GO.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-GO.tar.zst) | 63,1 MB |
| Maranhão (MA) | [andorinha-MA.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-MA.tar.zst) | 36,7 MB |
| Mato Grosso (MT) | [andorinha-MT.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-MT.tar.zst) | 32,7 MB |
| Mato Grosso do Sul (MS) | [andorinha-MS.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-MS.tar.zst) | 25,2 MB |
| Minas Gerais (MG) | [andorinha-MG.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-MG.tar.zst) | 188,1 MB |
| Pará (PA) | [andorinha-PA.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-PA.tar.zst) | 33,6 MB |
| Paraíba (PB) | [andorinha-PB.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-PB.tar.zst) | 41,3 MB |
| Paraná (PR) | [andorinha-PR.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-PR.tar.zst) | 93,8 MB |
| Pernambuco (PE) | [andorinha-PE.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-PE.tar.zst) | 65,9 MB |
| Piauí (PI) | [andorinha-PI.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-PI.tar.zst) | 32,7 MB |
| Rio Grande do Norte (RN) | [andorinha-RN.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-RN.tar.zst) | 26,5 MB |
| Rio Grande do Sul (RS) | [andorinha-RS.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-RS.tar.zst) | 82,7 MB |
| Rio de Janeiro (RJ) | [andorinha-RJ.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-RJ.tar.zst) | 54,4 MB |
| Rondônia (RO) | [andorinha-RO.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-RO.tar.zst) | 10,0 MB |
| Roraima (RR) | [andorinha-RR.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-RR.tar.zst) | 3,9 MB |
| Santa Catarina (SC) | [andorinha-SC.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-SC.tar.zst) | 70,3 MB |
| São Paulo (SP) | [andorinha-SP.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-SP.tar.zst) | 197,9 MB |
| Sergipe (SE) | [andorinha-SE.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-SE.tar.zst) | 16,4 MB |
| Tocantins (TO) | [andorinha-TO.tar.zst](https://github.com/murkiriel/andorinha/releases/download/2026-11-09-c17dff60/andorinha-TO.tar.zst) | 18,9 MB |
<!-- downloads:end -->

### Usar

1. Leia o `catalogo.json` e escolha os pacotes: **sempre o `base`**, mais os estados por onde você vai rodar,
   inclusive os de origem, destino e paradas (ver "Viagens longas").
2. Baixe cada arquivo de `release_url` + `file` e confira o `sha256`.
3. Extraia **todos na mesma pasta**. Tiles de divisa vêm nos dois estados vizinhos: são o mesmo arquivo e podem
   ser sobrescritos.
4. Aponte o Valhalla para a pasta (`mjolnir.tile_dir`) e roteie.

```
curl -LO https://github.com/Murkiriel/andorinha/releases/download/<build_id>/andorinha-base.tar.zst
curl -LO https://github.com/Murkiriel/andorinha/releases/download/<build_id>/andorinha-GO.tar.zst
mkdir tiles && tar --zstd -xf andorinha-base.tar.zst -C tiles && tar --zstd -xf andorinha-GO.tar.zst -C tiles
```

```python
import json, valhalla   # pip install pyvalhalla==3.9.1
cfg = valhalla.get_config(tile_extract="", tile_dir="tiles")
cfg["service_limits"]["motorcycle"]["max_distance"] = 5_000_000.0   # o padrão recusa rota acima de 500 km
cfg["loki"]["use_connectivity"] = False   # obrigatório com pacotes parciais (ver "Viagens longas")
actor = valhalla.Actor(cfg)
rota = actor.route(json.dumps({"locations": [{"lat": -16.6869, "lon": -49.2648}, {"lat": -16.3281, "lon": -48.9534}],
                               "costing": "motorcycle", "directions_options": {"language": "pt-BR"}}))
```

No Android e no iOS, com o [`valhalla-mobile`](https://github.com/Rallista/valhalla-mobile) 0.6.4 (o primeiro da
série 3.9; o 0.6.3 é da série 3.6 e não lê estes pacotes):

```kotlin
val padrao = ValhallaConfigFactory.usingTilesDir(pastaDosTiles.absolutePath)
val config = padrao.copy(
    loki = padrao.loki?.copy(useConnectivity = false),
    serviceLimits = padrao.serviceLimits?.let { it.copy(motorcycle = it.motorcycle?.copy(maxDistance = 5_000_000)) },
)
val rota = Valhalla(context, config).use { it.routeRaw(requisicaoJson) }
```

**Não misture gerações.** Os tiles de um estado apontam para os dos vizinhos. Todos os pacotes de uma pasta
precisam ter o mesmo `build_id`; ao atualizar, baixe de novo todos os que estiverem instalados.

**Viagens longas.** Longe da origem e do destino, o Valhalla só usa as rodovias do pacote base. Uma viagem entre
estados funciona com a base e os pacotes da origem e do destino, **desde que a config tenha
`loki.use_connectivity: false`**. Essa checagem prévia olha só as vias locais: sem os estados do meio, ela declara
as pontas "em regiões desconectadas" e nem tenta a rota. Desligada, Porto Alegre → Fortaleza só com base + RS + CE
dá a mesma rota do Brasil inteiro (4.278,5 km na validação da geração `2026-11-09-c17dff60`, Valhalla 3.9.1).

**O pacote base sozinho não roteia.** Ele é a ligação entre os trechos do meio da viagem; **origem, destino e
paradas precisam estar numa UF instalada**. Com a base e o DF, um destino em Goiânia (sem o pacote de GO) não gera
rota: o Valhalla responde "No suitable edges near location", porque as rodovias da base não servem para ancorar o
ponto. Aumentar o raio de busca (`search_cutoff`) não resolve: a rota sai, mas ancora no lugar errado sem avisar
(no teste, com 100 km, terminou na GO-139, com 144 km, em vez dos 213,9 km da rota real). Antes de pedir a rota,
confira se cada ponto cai numa UF instalada e, se não cair, peça o pacote dela.

## Compatibilidade

| Andorinha | Valhalla | Bibliotecas que leem |
|---|---|---|
| gerações atuais | **3.9.1** | `valhalla_service` 3.9.1, `pyvalhalla` 3.9.1, [`valhalla-mobile`](https://github.com/Rallista/valhalla-mobile) 0.6.4 (Android e iOS) |
| gerações anteriores à troca | série 3.6 | `valhalla-mobile` 0.6.3 |

Os tiles precisam ser lidos pela versão do Valhalla que os gerou: o formato muda entre versões. A versão de cada
geração está no campo `valhalla_version` do `catalogo.json`: confira antes de baixar e recuse uma geração de
versão diferente da do seu motor.

Os pacotes passaram da série 3.6 para a 3.9 na geração `2026-11-09-c17dff60`, para acompanhar o `valhalla-mobile`
0.6.4 (de 2026-10-06), o primeiro a embutir a série 3.9. O formato do tile mudou nessa troca: os pacotes novos não servem para um motor da série 3.6 (como o
`valhalla-mobile` 0.6.3), nem os anteriores para um motor 3.9. Quem tem pacotes de antes da troca precisa baixar
todos de novo, com o motor novo (o que falta e o histórico estão em [`gerador/PENDENCIAS.md`](gerador/PENDENCIAS.md)).

## Formato

### O que tem em cada pacote

| Pacote | Conteúdo |
|---|---|
| `andorinha-base.tar.zst` | Nível 0 do Valhalla: motorway, trunk e primary do Brasil inteiro (quadrados de 4°) |
| `andorinha-<UF>.tar.zst` | Níveis 1 (secondary e tertiary, quadrados de 1°) e 2 (demais vias, quadrados de 0,25°) que tocam a UF, pela malha oficial do IBGE com ~5 km de margem |

Tamanho, sha256, número de tiles e retângulo de cada pacote estão no `catalogo.json`.

Os pacotes são **`.tar.zst`** (zstd nível 19, janela de 8 MB): cerca de 15% menores que o `.tar.gz` de antes e mais
rápidos de descompactar, com pouca memória (num celular, o pacote do DF abriu em 0,27 s). Até 2026-10-05 eram
`.tar.gz`; a geração `2026-10-05-93a54bc6` saiu com os dois formatos e a chave `zstd` em cada pacote do catálogo.

Desde as gerações seguintes a 2026-10-05, os tiles levam o **fuso horário** de cada cruzamento (as 16 zonas do
Brasil): uma rota pedida com `date_time` (hora de partida ou de chegada) responde com a hora de cada ponto e o
fuso (`time_zone_name`, `time_zone_offset`). Não há arquivo a mais para baixar.

### `catalogo.json`

```
schema             versão deste formato (1)
build_id           a geração: data + 8 primeiros do md5 do extrato (ex.: 2026-09-30-cb859dc3), também o nome da release
built_at           quando foi gerada (UTC)
valhalla_version   versão do Valhalla que gerou (e que precisa ler) os tiles
generator_commit   commit do gerador que produziu a geração
required_config    ajustes obrigatórios na config do Valhalla de quem usa os pacotes: {"chave.pontuada": valor}
osm                data (timestamp), md5 e endereço (url) do extrato do OpenStreetMap usado
release_url        onde baixar os arquivos desta geração
attribution        crédito obrigatório dos dados
timezones          fusos horários dentro dos tiles: release do timezone-boundary-builder e as zonas (as 16 do Brasil)
base               o pacote base (campos abaixo)
states             {UF: pacote}, um por estado (campos abaixo)
contract           o mesmo catálogo no contrato comum aos quatro geradores (v1): ver docs/CATALOGO.md#andorinha
  name             nome do estado (só em states)
  file             nome do arquivo na release
  bytes            tamanho do arquivo compactado
  sha256           para conferir o download
  tiles            quantos tiles o pacote tem
  bytes_tiles      tamanho dos tiles descompactados
  bbox             área que o pacote atende: o polígono da UF no IBGE (na base, o Brasil),
                   [lat mín, lon mín, lat máx, lon máx], arredondado para fora; use para escolher o que baixar
  bbox_tiles       área coberta pelos tiles do pacote: em geral maior que o bbox (os tiles de 1° e 4° passam da
                   divisa), mas menor onde a UF não tem via (ilhas oceânicas, extremo norte da Amazônia)
```

**Coordenadas e ordem do `bbox`.** As coordenadas são WGS84 (EPSG:4326), em graus decimais. `bbox` e `bbox_tiles`
vêm com a **latitude primeiro**: `[lat_min, lon_min, lat_max, lon_max]` (no DF, `[-16.05, -48.29, -15.5, -47.3]`).
É o contrário do GeoJSON, que usa `[lon_min, lat_min, lon_max, lat_max]`: não passe o `bbox` direto para uma
biblioteca de GeoJSON. A ordem fica assim no `schema` 1; se um dia mudar, será num campo de nome novo, para que um
leitor antigo não leia a ordem errada sem perceber.

Hoje, `required_config` é `{"loki.use_connectivity": false, "service_limits.motorcycle.max_distance": 5000000}`
(ver "Viagens longas"). Quem usa os pacotes pode aplicar essas chaves direto na config do Valhalla.

## Cobertura e limitações

- **O Brasil inteiro**, nas 27 UFs, e só ele: rota que sai do país não tem via do outro lado.
- **Origem, destino e paradas precisam de uma UF instalada**; a base sozinha não ancora um ponto (ver "O pacote
  base sozinho não roteia").
- **Com pacotes parciais, `loki.use_connectivity` tem de estar desligado** (ver "Viagens longas"), e o limite de
  distância da moto, aumentado: as duas chaves vêm em `required_config`.
- **Uma versão do motor por geração**: os tiles só servem para o Valhalla que os gerou (ver "Compatibilidade"), e
  pacotes de gerações diferentes não se misturam.
- **O que a validação cobre:** 33 rotas por geração, com foco na moto; carro, bicicleta e a pé têm uma rota curta
  cada. O resto do país não é conferido rota a rota.
- **Os dados são os do OpenStreetMap do dia do extrato**, com as lacunas e os erros dele (vias que faltam, sentido e
  restrições errados).

## Geração e reprodutibilidade

O código está em [`gerador/`](gerador) (Python): baixa o extrato, roda o Valhalla, divide por estado, testa rotas
com os pacotes separados e monta o catálogo. Lá estão as medições e as decisões (versão, threads, velocidades).

Cada geração só é publicada se 33 rotas derem, com os pacotes separados, a mesma distância que com o Brasil
inteiro. O foco é a moto: uma rota dentro de cada estado, duas entre estados vizinhos e uma viagem longa só com
os pacotes das pontas. Carro, bicicleta e a pé têm uma rota curta cada.

O conjunto é gerado de novo **toda semana, no domingo** (se falhar, nos dias seguintes, até sair), com o extrato do
OpenStreetMap mais recente, pelo GitHub Actions (workflow [`gerar`](.github/workflows/gerar.yml), com disparos às
03:17 e 09:17 de Brasília). Se a geração falhar, a publicada continua no ar e fica aberta uma
[issue](https://github.com/Murkiriel/andorinha/issues) "Geração falhando" até a próxima dar certo. Cada geração é uma
release nova; o `catalogo.json` da raiz sempre aponta para a mais recente, e a data do OpenStreetMap usado está no
campo `osm.timestamp`. Cada release leva também o `catalogo.json` da sua geração (a partir das gerações seguintes a
2026-11-11), para quem precisa do índice de uma geração que já não é a da raiz.

Ficam no ar as **12 gerações mais recentes** (~3 meses); as anteriores são apagadas a cada publicação, e os links
delas deixam de funcionar. Quem instalou uma geração consegue baixar mais estados dela enquanto ela estiver entre as 12; depois
disso, precisa atualizar todos os pacotes para a geração do catálogo. Use sempre o `release_url` do `catalogo.json`,
não um link fixo de release.

**Só a geração corrente é reproduzível.** Enquanto o extrato do OpenStreetMap usado ainda está na fonte, o gerador do
commit `generator_commit` refaz a geração com as mesmas entradas: o Python 3.12, o Valhalla e as dependências Python
travados com hashes ([`gerador/requirements.in`](gerador/requirements.in) → [`gerador/requirements.txt`](gerador/requirements.txt),
instalados com `pip install --require-hashes`), as actions do GitHub fixadas pelo SHA do commit, a malha do IBGE
versionada no repositório e o zip de fusos conferido pelo sha256. O extrato, não: ele vem do arquivo "latest" do dia,
que a fonte troca e que o Andorinha não arquiva. O catálogo registra o md5 (`osm.md5`), a data e o endereço do extrato
usado; com isso dá para conferir um extrato, não recuperá-lo. Uma geração antiga não pode ser refeita depois que a
fonte troca o arquivo; os pacotes dela continuam nas releases enquanto ela estiver entre as 12. O SpatiaLite do build
vem do `apt` da imagem `ubuntu-24.04` do Actions, sem versão fixa.

## Licença e atribuição

Os **pacotes** são uma base derivada do OpenStreetMap, distribuída sob a **Open Database License (ODbL) 1.0**
(texto em [`LICENSE`](LICENSE)).

- Cite "© colaboradores do OpenStreetMap".
- Se você publicar uma base derivada destes pacotes, ela precisa manter a ODbL.
- Recorte por estado: malha das UFs do IBGE, guardada em [`gerador/ibge_malha_uf.json`](gerador/ibge_malha_uf.json)
  (dado do IBGE, não parte do código).

O texto do crédito está no `attribution` do `catalogo.json`, e cada release leva também um `LICENCA-DADOS.txt`, com a
atribuição, as fontes daquela geração e o texto inteiro da ODbL, para quem baixar só os pacotes. Ele fica fora dos
`.tar.zst`, que só têm tiles.

O **código** (pasta `gerador/`) é **MIT** ([`gerador/LICENSE`](gerador/LICENSE)). O Valhalla é MIT.

## Projetos irmãos

Quatro geradores independentes de dados abertos do Brasil para uso offline, um pacote por UF:

- [**Carcará**](https://github.com/Murkiriel/carcara): o mapa base, em PMTiles.
- **Andorinha** (este): as rotas, em tiles do Valhalla.
- [**Sabiá**](https://github.com/Murkiriel/sabia): a busca de lugares e endereços, em SQLite.
- [**Pardal**](https://github.com/Murkiriel/pardal): os radares e os limites de velocidade, em CSV, GeoJSON, KML, GPX
  e SQLite.

Os quatro dividem o país nas mesmas 27 UFs, pelos limites do IBGE, e publicam um `catalogo.json` com o `sha256` de
cada arquivo e o `bbox` de cada UF em `[lat_min, lon_min, lat_max, lon_max]`, no WGS84. Carcará, Andorinha e Sabiá
seguem o mesmo molde de catálogo e de release (`schema` 1, `build_id` como etiqueta da release, `release_url`,
`base` e `states`) e dão o mesmo `bbox` para cada UF; o catálogo do Pardal tem formato próprio (`ufs`). O Andorinha
e o Sabiá leem o mesmo extrato do OpenStreetMap, e com o mesmo extrato o `build_id` dos dois termina com os mesmos 8
caracteres do md5 (`c17dff60` nas gerações publicadas em 2026-11-11).

## English

**Andorinha** publishes ready-to-use **Valhalla routing tiles for Brazil**, rebuilt every week (on Sunday) from
OpenStreetMap and split into a base pack (the country's highways) plus one pack per state. Download the base and the
states you need, extract them into one folder and point Valhalla's `mjolnir.tile_dir` at it. Tiles are built with
Valhalla **3.9.1** and must be read by that version (e.g. `valhalla-mobile` 0.6.4 on Android/iOS; 0.6.3 ships Valhalla 3.6 and cannot read them). All packs in a folder
must share the same `build_id`. The base pack only links the middle of a trip: origin, destination and stops need
their state pack installed. The catalogue `bbox` is WGS84, latitude first (`[lat_min, lon_min, lat_max, lon_max]`,
unlike GeoJSON). Only the current generation is reproducible: dependencies are locked with hashes and actions pinned
by commit SHA, but the "latest" OpenStreetMap extract is not archived. Data under the ODbL 1.0 (© OpenStreetMap
contributors); generator code under MIT. Sister projects: basemap ([Carcará](https://github.com/Murkiriel/carcara)),
search ([Sabiá](https://github.com/Murkiriel/sabia)) and speed cameras and limits
([Pardal](https://github.com/Murkiriel/pardal)).

<sub>Palavras-chave: Valhalla Brasil, tiles Valhalla, roteamento offline, navegação offline, rotas offline Brasil,
OpenStreetMap Brasil, grafo de rotas, moto, carro, bicicleta, pyvalhalla, valhalla-mobile.</sub>
