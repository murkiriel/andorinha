# Gerador do Andorinha

Código que gera os pacotes de tiles e o `catalogo.json` a partir do extrato do Brasil no OpenStreetMap.

## Requisitos

- Python 3.12 ou mais novo (testado com 3.12): as rodas do `pyvalhalla` 3.9.1 são `cp312-abi3`, e o `numpy` travado
  no `requirements.txt` também pede 3.12
- `pip install --require-hashes -r requirements.txt`: `pyvalhalla` (o Valhalla, com os executáveis nativos, inclusive
  para Windows), `shapely` e `zstandard`. Num Python sem venv (a distribuição "embeddable" do Windows), use
  `pip install --require-hashes --target .pylib -r requirements.txt`; o `generate.py` põe `.pylib/` no caminho de
  import. As dependências diretas ficam em `requirements.in`; o `requirements.txt` é gerado dele pelo `uv` (o comando
  está no topo dos dois arquivos), com todas as dependências, transitivas inclusive, em versão fixa e com o sha256 de
  cada arquivo: o `pip` recusa um arquivo que não bata. Os testes do Actions usam `requirements-testes.txt`, gerado do
  mesmo jeito, sem o `pyvalhalla` e nas mesmas versões (`tests/test_requirements.py` confere).
- ~25 GB de disco livre (o build confere antes de começar e para na hora se faltar; o pico medido em `data/` foi
  17,5 GB) e memória conforme as threads: pico medido de **20,3 GB com 8 threads** e **13,4 GB com 2** (a leitura
  do extrato sozinha passa de 7 GB). O build usa as threads da máquina, no máximo 8 (`ANDORINHA_THREADS` muda)
- ~2,3 GB de download por geração (o extrato só é baixado de novo quando há versão nova)

## Como rodar

Da pasta `gerador/`:

```
python generate.py                   # geração completa em data/dist/
python generate.py --from packages   # reaproveita os tiles de data/build/
python scripts/publish.py            # confere a geração (não publica nada)
python scripts/publish.py --release  # sobe os pacotes numa release e grava o catalogo.json na raiz
python scripts/due.py                # está na hora de gerar de novo? (só responde; não gera)
python scripts/weights.py study --release <build_id> --out data/pesos  # o lote de pesos da semana, sobre uma release publicada (demora horas)
python scripts/weights.py upload --tag <build_id> --out data/pesos     # sobe o resultado como assets extras da mesma release
python -m unittest discover -s tests -t .
```

Os testes também rodam no GitHub Actions a cada push e pull request (`.github/workflows/testes.yml`), sem o
pyvalhalla e sem os dados baixados: os 3 testes que dependem deles são pulados, com o motivo no log.

A geração inteira roda no Actions, no workflow `gerar` (`.github/workflows/gerar.yml`), **toda semana, no
domingo**: ele dispara todo dia às 03:17 e às 09:17 de Brasília, o job `interval` pergunta ao `scripts/due.py` se a
geração publicada (`built_at` do `catalogo.json`) é de antes do último domingo, 00:00 em Brasília, e só então o build
roda, estuda o lote de pesos e publica. Se a geração falhar, o disparo seguinte tenta de novo, e os dias seguintes também, até sair; uma
geração à mão no meio da semana não adia a do domingo. Uma execução agendada que falha abre a issue **"Geração
falhando"** (ou comenta nela, se já estiver aberta), com o link da execução; a primeira que gerar e publicar comenta e
fecha a issue (job `alert`, `scripts/alert.py`). Quem acompanha o repositório recebe a notificação. O mesmo job
cuida de mais duas issues: **"Estudo dos pesos falhando"** (o job `weights` falhou; os pesos pararam de se renovar,
mas nada do que foi publicado muda) e **"Publicação parada"** (a geração publicada tem mais de 9 dias, os 7 do ciclo
e 2 de folga; é conferida em todo disparo agendado, mesmo quando o `interval` diz "ainda não", e pega o agendamento
que não dispara, o `interval` que erra e a release que não sobe). O `alert` roda por último, depois do `weights`. São dois horários, fora da hora cheia, porque o GitHub atrasa ou pula disparos
agendados sem avisar: o segundo cobre o primeiro e, se o primeiro já publicou, só confere a data e para. Disparado à
mão, gera na hora e só publica com `publish` marcado. Num fork, o agendamento não gera.

O workflow tem dois jobs depois do `interval`, em fila. O `generate` libera espaço apagando ferramentas pré-instaladas da
máquina, põe os dados e 12 GB de swap no disco com mais espaço livre, gera, guarda os logs e as medições (memória, swap
e disco a cada 30 s) como artefato de diagnóstico (se a cota de artefatos da conta acabar, esse upload falha sem derrubar
o job) e publica pelo `publish.py`, como sempre. **Nada da geração passa por artefato do Actions.** O `weights` roda
depois, só se o `generate` publicou: baixa os pacotes da release recém-publicada pelas URLs públicas, refaz o lote de
pesos da semana (ver [Pesos da moto por município](#pesos-da-moto-por-município)) e sobe o resultado como assets extras
da mesma release. Falhar ou estourar o tempo não muda nada do que foi publicado. O repositório das releases é o do próprio
Actions (`GITHUB_REPOSITORY`), então o mesmo workflow serve num repositório de teste e no público.

Medido no Actions em 2026-09-30, na máquina de repositório privado (2 CPUs, 7,8 GB de RAM), geração completa com
publicação: **30,5 min** — extrato 1,8 min, tiles 24,0 min com 2 threads, pacotes 4,1 min, validação 0,5 min — e
mais 1,8 min para subir a release. Pico de 3,9 GB de RAM usada (sem contar o cache de arquivos) e 0,6 GB de swap,
em amostras a cada 30 s; 16,0 GB de disco em `data/`. As máquinas são compartilhadas: em outras execuções do mesmo
dia os tiles levaram de 23 a 40 min.

Cada geração grava `data/dist/geracao.json`: minutos de cada etapa, pico de disco usado em `data/` (amostrado a
cada 20 s, para pegar os intermediários do Valhalla) e o resultado. `--from catalog` só remonta o catálogo, sem
validar (avisa no log).

Geração completa medida em 2026-09-30 (`geracao.json`, máquina de 16 threads e 32 GB, sem download novo do
extrato): **20,8 min** — tiles do Brasil 15,5 min, pacotes 3,9 min, validação das 30 rotas 1,4 min. Com o extrato
novo, somar o download de 2,3 GB (25 min numa conexão de ~1,5 MB/s).

## Como funciona

| Etapa | Módulo | O que faz |
|---|---|---|
| (antes de tudo) | `andorinha/sources.py` | Confere a malha das UFs do IBGE, a cópia versionada `ibge_malha_uf.json`: as 27 UFs e o sha256 de `config.py`. Nada é baixado (a API do IBGE deixou de responder ao Actions em 2026-11-03), e a mesma malha dá sempre a mesma divisão dos pacotes. Vem primeiro para uma falha aparecer em segundos, não depois dos tiles. Para trocar a cópia, ver `IBGE_MESH_URL` em `config.py` |
| (antes de tudo) | `andorinha/municipalities.py`, `andorinha/weights_tables.py` | Confere a malha municipal do IBGE, `ibge_malha_municipios.json.gz` (cópia versionada: sha256 do conteúdo e os 5.570 municípios), e as duas tabelas de pesos da moto de que a geração parte: as da release da geração publicada (as `.proximo`, que o estudo dela devolveu, ou, sem elas, as normais, `pesos_moto_municipios.csv` e `pesos_moto_estados.csv`; conferidas pelo sha256 e pela contagem, pelo formato, pela malha e por não perderem linhas) ou, se a release não as tem ou elas não conferem, a semente do repositório (sha256 e contagem do `config.py`). A malha e a semente não vêm da rede; a tabela da release, sim (uma tentativa de download e, se falhar, a semente com um aviso). Ver [Pesos da moto por município](#pesos-da-moto-por-município) |
| osm | `andorinha/sources.py` | Baixa o extrato do Brasil quando há versão nova e confere o md5 publicado. Fonte principal: openstreetmap.fr (se ele troca o extrato durante o download, uma vez por dia, lê o md5 de novo e baixa a versão nova uma vez). Se ele falha, a Geofabrik, por um arquivo datado (`brazil-AAMMDD.osm.pbf`, que não muda): o que o `-latest` aponta (dois redirecionamentos) e, depois, até 3 datados da listagem da pasta, do mais novo ao mais velho. O datado do dia aparece na listagem horas antes de responder (404, ou 502 no `.md5`): o `.md5` é pedido uma vez só, sem esperar, e quem não responde passa ao anterior. A data e a hora do extrato vêm do cabeçalho do próprio arquivo (`andorinha/pbf_header.py`), não do `state.txt`; se o cabeçalho não der, vale a meia-noite do dia do nome, com aviso. O log avisa e o `url` do catálogo diz de onde veio. `ANDORINHA_OSM_SOURCES` muda a ordem (no Actions, a entrada `osm_sources` do disparo manual). Extrato mais velho que 36 h no momento da geração (`OSM_MAX_AGE_HOURS`) vira nota no `geracao.json` e aviso no log ("OSM de AAAA-MM-DD, N h mais velho que o momento da geração"); não barra |
| tiles | `andorinha/builder.py` | Roda `valhalla_build_admins` e `valhalla_build_tiles` do pyvalhalla, com 8 threads (config e executáveis vêm do `engine.py`), com o banco de fusos só do Brasil de `andorinha/timezones.py` (desde 2026-10-05; o zip da release fixada do timezone-boundary-builder é conferido pelo sha256 de `config.py` e recusado se não bater; precisa do SpatiaLite, `mod_spatialite`) |
| packages | `andorinha/packing.py` | Divide os tiles em base (nível 0) e um pacote por UF (níveis 1 e 2), pela malha do IBGE, e grava os `.tar.zst` (zstd 19; até 2026-10-05, `.tar.gz`) |
| packages | `andorinha/municipalities.py` | Grava o arquivo de apoio de cada UF, `andorinha-municipios-<UF>.geojson.zst`: o contorno de cada município (malha municipal do IBGE), o peso do perfil de moto dele e, no cabeçalho, o peso do estado, e grava ao lado as duas tabelas de pesos de que partiu. O estudo dos pesos não roda aqui, e sim no job `weights`, depois; ver [Pesos da moto por município](#pesos-da-moto-por-município) |
| weights (job à parte, depois da publicação) | `andorinha/weights_study.py`, `andorinha/weights_run.py`, `andorinha/weights_tables.py`, `scripts/weights.py` | `study`: baixa a release recém-publicada, escolhe o lote da semana (600 municípios e 1 estado de linha mais velha), refaz os pesos com o Valhalla (4 processos no Actions) e grava as tabelas `.proximo` e o `pesos_proximo.json`. `upload`: sobe esses três arquivos na mesma release, sem mexer em mais nada dela |
| validate | `andorinha/validation.py` | 33 rotas, cada uma só com a base e as UFs dela instaladas, comparadas com a rota do Brasil inteiro. O foco é a moto, com 30: uma dentro de cada UF (capital -> cidade de 25 a 140 km), duas entre UFs vizinhas e Porto Alegre -> Fortaleza só com os pacotes das pontas. Carro, bicicleta e a pé têm uma rota curta cada, dentro de uma UF. Leva ~1 min |
| catalog | `andorinha/catalog.py` | Monta o `catalogo.json` com tamanho, sha256, bbox e contagem de tiles de cada pacote, e os arquivos de apoio dos municípios (chave `municipalities`), com a geração dos tiles de que vieram os pesos de cada UF |

Módulos de apoio:
- `andorinha/engine.py`: o único que importa o pyvalhalla. Confere a versão em toda importação e guarda as duas configs:
  a do build e a de quem usa os pacotes (`routing_config`, com `use_connectivity` desligado e o limite de distância da
  moto aumentado), que a validação usa.
- `andorinha/publishing.py`: as travas da publicação (`check`, `drops`); `scripts/publish.py` é só a linha de comando.
- `andorinha/weights_study.py`, `weights_tables.py`, `weights_run.py`: o estudo dos pesos em lotes (o método, a rotação e a
  conferência das tabelas, a execução de um lote); `scripts/weights.py` é só a linha de comando.
- `andorinha/schedule.py`: a regra semanal (gera se o `built_at` do `catalogo.json` publicado é de antes do último
  domingo, 00:00 em Brasília); `scripts/due.py` é só a linha de comando. Até 2026-11-08 era a cada 29 dias.
- `andorinha/tiles.py`: a grade de tiles (caminho do arquivo, nível, índice e retângulo).
- `andorinha/net.py`: downloads que retomam de onde pararam (`Range`), nunca deixam arquivo pela metade com o nome final e só repetem erro passageiro (4xx, fora 408 e 429, falha na hora).
  Corpo comprimido (gzip) é descomprimido ao gravar: o IBGE manda a malha assim para os executores do Actions,
  mesmo sem o urllib pedir.
- `andorinha/run_record.py`: o `geracao.json` (tempos, pico de disco, resultado e `notes`, os avisos de uma geração
  que não falhou).
- `andorinha/util.py`: hash de arquivo (sha256 dos pacotes, md5 do extrato).
- `andorinha/config.py`: tudo o que muda o resultado de uma geração.

## Decisões e medições

**Versão do Valhalla fixada em 3.9.1.** O formato do tile muda entre versões (o cabeçalho mudou na 3.8), e os
tiles precisam ser lidos pelo mesmo Valhalla que os gerou. 3.9.1 é a versão embutida no `valhalla-mobile` 0.6.4,
a biblioteca Android e iOS (de 2026-10-06; até a troca, os pacotes eram da 3.6.3, a do `valhalla-mobile` 0.6.3). O `builder.py` recusa rodar com outra versão do pyvalhalla instalada. Para trocar:
mude `VALHALLA_VERSION` e o `requirements.in` juntos, gere o `requirements.txt` de novo (o comando está no topo
dele), gere tudo de novo e publique como geração nova.

**8 threads, não todas.** Numa máquina Windows de 16 threads, o `valhalla_build_tiles` com todas caiu duas vezes
sem escrever nada no log (código `0xC0000409`), com o Brasil e com o DF, no Valhalla 3.6.3 e no 3.8.3. Com 8
terminou todas as vezes. O Valhalla 3.9 passou a limitar uma ordenação interna a 8 threads.

**Sem tabela de velocidades padrão (`default_speeds_config`).** As velocidades vêm do `maxspeed` do OpenStreetMap
e das regras por tipo de via do próprio Valhalla. Num teste no DF, tiles gerados com uma tabela de velocidades
por densidade urbana estimaram 39 km/h para a moto na DF-002 (Eixão, via expressa de 80 km/h), contra 73 km/h sem
a tabela, e quase o dobro do tempo na rota inteira.

**Com fusos horários; sem trânsito, transporte público e pontos de referência.** O pacote é o grafo de rotas com o
fuso de cada nó (desde 2026-10-05: o banco só do Brasil, de `andorinha/timezones.py`, entra no build), então uma rota
com hora marcada (`date_time`) responde com a hora e o fuso de cada ponto, sem arquivo a mais para quem usa. Trânsito,
transporte público e pontos de referência ficam de fora.

**Divisão por UF.** O nível 0 (motorway, trunk e primary) vai inteiro para o pacote base: longe da origem e do
destino, o Valhalla só usa esse nível, então uma viagem longa funciona com a base e os pacotes das duas pontas. Os
níveis 1 e 2 vão para as UFs cujo polígono (malha do IBGE, com margem de 0,05°, ~5 km) toca o tile. Tile na divisa
entra nas duas UFs. Os tiles que não tocam nenhuma UF (sobras do recorte do extrato em países vizinhos) ficam de
fora.

**Pacotes parciais exigem `loki.use_connectivity: false`.** A checagem prévia de conectividade do Valhalla olha só as vias locais: com a base e os pacotes das duas pontas, sem os estados do meio, ela recusava Porto Alegre → Fortaleza ("regiões desconectadas") sem tentar. Desligada, sai a mesma rota do Brasil inteiro (4.278,5 km na geração `2026-11-09-c17dff60`, Valhalla 3.9.1; eram 4.242 km no 3.6.3). A validação roda assim, e o README da raiz manda quem usa fazer o mesmo.

**Contornos e pesos fora do pacote de tiles.** Os contornos dos municípios e os pesos da moto trocam junto com os
tiles, e o lugar natural seria dentro do `.tar.zst` de cada UF. Não entram lá porque um leitor que já existe confere o
pacote ao instalar: recusa qualquer entrada do tar que não tenha nome de tile (`1/026/772.gph`) e compara a contagem e
a soma dos tamanhos com `tiles` e `bytes_tiles` do catálogo. Um arquivo a mais faria a instalação falhar em quem não
atualizou o leitor. Por isso cada UF tem um arquivo à parte na release, descrito no catálogo só com chaves novas
(`municipalities` e, no bloco `contract`, arquivos `auxiliary`), que um leitor antigo ignora.

**Pacotes reproduzíveis.** Ordem fixa e datas zeradas no `.tar` (o zstd não guarda nome nem data): os mesmos tiles
geram o mesmo arquivo, byte a byte, e o mesmo sha256.

**GitHub Releases, não git.** Os pacotes são binários de centenas de MB que mudam por inteiro a cada geração.
No git, o histórico incharia a cada geração. Cada geração vira uma release (`build_id` = data + md5 do extrato), e o repositório guarda
só o gerador e o `catalogo.json` da geração mais recente (o de cada geração vai também na release dela). Limites do GitHub Releases: cada arquivo abaixo de 2 GiB, sem limite de tamanho
total nem de banda.

**Ficam 12 gerações** (~3 meses de gerações semanais, ~16 GB). No fim de cada publicação, depois do push do
`catalogo.json` novo, o `publish.py` apaga as releases antigas (e as tags delas): ficam a do catálogo e as 11
anteriores mais recentes (`KEEP_GENERATIONS`; `ANDORINHA_KEEP_GENERATIONS=0` desliga). Várias, e não uma, porque
pacotes de gerações diferentes não se misturam: quem instalou uma geração anterior só consegue baixar mais um estado
dela enquanto a release existir. Eram 3 enquanto a geração era a cada 29 dias (a mesma janela, ~87 dias). Também saem os
rascunhos órfãos (uma subida que caiu deixa um rascunho que a geração seguinte, com outro `build_id`, não retoma).
Release com tag fora do formato do `build_id` nunca é tocada; se a release do catálogo não estiver publicada, nada
é apagado. Uma falha na limpeza vira aviso e não derruba a publicação: a geração seguinte tenta de novo.
`python scripts/publish.py --prune --dry-run` mostra o que seria apagado.

## Pesos da moto por município

`pesos_moto_municipios.csv` diz, para cada município, as opções `use_highways` e `use_trails` do costing `motorcycle`
do Valhalla (com `use_tolls` 1,0) que deram o menor tempo médio numa amostra de viagens **dentro do município**. Serve
a um leitor que quer escolher o peso quando a origem e o destino caem no mesmo município. Desde 2026-11-15 o próprio
gerador refaz o estudo, **em lotes, a cada geração**, sobre os tiles dela (ver "O estudo em lotes" abaixo); a cópia do
repositório é só a semente de onde a primeira geração parte.

Colunas, separadas por `;`: `codigo_ibge` (7 dígitos), `use_highways`, `use_trails`, e com o que **aquela linha** foi
calculada: `geracao_tiles` (o `build_id` dos tiles), `versao_valhalla` e `data_calculo`. A origem vai por linha porque
a tabela é refeita aos pedaços: um lote troca só as linhas dele, e o catálogo diz, por UF, de que gerações vieram os
pesos (`municipalities.states.<UF>.generations`).

### O método

É o dos estudos de 2026-11-14 que geraram a semente (5.564 municípios, todos com os tiles da geração
`2026-11-09-c17dff60` e o `pyvalhalla` 3.9.1), reescrito em `andorinha/weights_study.py`:

1. **Amostra.** Em cada município, 32 origens e 32 destinos sorteados de modo uniforme dentro do contorno (malha
   municipal do IBGE), cada um a até 150 m de uma via: 1.024 pares. O sorteio é determinístico (semente
   `20261120` mais o código do município) e é o mesmo dos estudos: com os mesmos tiles, o mesmo município dá os mesmos
   pontos. Um município que não dá nenhum ponto a até 150 m de uma via em 50.000 sorteios fica sem peso (o leitor usa o
   padrão): hoje são cinco, AM 1300201, 1301951, 1302108 e 1302801 e PA 1500701. Fernando de Noronha (PE 2605459) ficou
   de fora do estudo de 2026-11-14 por uma categoria externa ("sem sede"); o gerador não usa essa categoria e o
   estuda: deu 0,6/0,4.
2. **Grade.** Cada par é roteado com `use_highways` em 0,2, 0,4, 0,6 e 0,8 e `use_trails` em 0,2 e 0,4, com
   `use_tolls` 1,0 e a config de quem usa os pacotes (`engine.routing_config`: `loki.use_connectivity` desligado e o
   limite de distância da moto em 5.000 km). Só contam os pares que têm rota em todas as combinações; um par sem rota
   na primeira combinação (ponto isolado) nem é tentado nas outras.
3. **Teto.** `use_trails` fica limitado a 0,4 (`MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS`), por decisão do estudo. Os estudos
   de 2026-11-14 roteavam também 0,6 e 0,8 e descartavam essas combinações depois; o gerador nem as roteia, o que
   corta a grade pela metade sem mudar quem vence (ver "Conferência" abaixo).
4. **Centro.** Entre as 8 combinações, a de menor tempo médio; no empate, a de menor distância média e, depois, a mais
   perto de 0,6/0,4.
5. **Refino.** As vizinhas a 0,1 do centro (3 x 3 em volta, menos ele, com `use_trails` até 0,4) são roteadas nos
   mesmos pares; vence o menor tempo médio, e no empate fica o centro.

Resultado da semente: 3.787 municípios em 0,6/0,4, 520 em 0,4/0,4, 378 em 0,8/0,4 e os demais espalhados de 0,1 a
0,9 em `use_highways` e de 0,1 a 0,4 em `use_trails`.

### O estudo em lotes

O país inteiro leva ~3,5 CPU-horas por 600 municípios (ver "Tempo" abaixo): não cabe numa execução só junto com a
geração, então cada geração refaz um lote.

- **Onde roda: depois da publicação, sem artefato.** Num job à parte do workflow `gerar`, o `weights`, que só roda se o
  `generate` publicou. O `generate` publica como sempre, com as travas de sempre (o estudo não está no caminho dele).
  O `weights` baixa da release recém-publicada, pelas URLs públicas, o `catalogo.json`, a base e as 27 UFs (conferidos
  pelo sha256 do catálogo) e as duas tabelas de que a geração partiu, extrai os pacotes numa pasta só (são os tiles
  do Brasil, os mesmos que o leitor usa) e roda `python scripts/weights.py study --release <build_id> --out pesos`.
  Depois `python scripts/weights.py upload --tag <build_id> --out pesos` (com o `GITHUB_TOKEN` do job, o único com
  essa permissão) sobe o resultado. **Se o estudo falhar ou estourar o tempo, nada do que foi publicado muda**, a
  geração seguinte parte das tabelas normais da release e o job `weights` aparece em vermelho; a falha não abre a issue
  "Geração falhando", e sim a "Estudo dos pesos falhando" (o `alert` lê o `weights` à parte, comenta nas falhas
  seguintes e fecha na primeira vez que o estudo passar). Um artefato pequeno de diagnóstico (`pesos`, com o `estudo.json`
  e o `/usr/bin/time -v`) sai com `continue-on-error`, e nada depende dele.
- **O que sobe na release (assets extras da MESMA release).** `pesos_moto_municipios.proximo.csv`,
  `pesos_moto_estados.proximo.csv` e `pesos_proximo.json` (o `build_id` da release, a versão do Valhalla, o nome, o
  `sha256` e a contagem de cada tabela e um resumo do estudo: dia, lote, tempo, processos). O `upload` confere que as
  tabelas são as que o json descreve, sobe com `--clobber` só esses três nomes (o json por último, para a geração
  seguinte ignorar o que ficar pela metade) e confere que chegaram com o tamanho certo. Não reescreve o `catalogo.json`
  nem nenhum asset já publicado: o `sha256` dos arquivos de apoio por UF e das tabelas normais continuam sendo os do
  catálogo.
- **As tabelas normais e as próximo.** Toda geração publica, ao lado dos pacotes, a tabela de que ela partiu
  (`pesos_moto_municipios.csv` e `pesos_moto_estados.csv`, com o `sha256` e a contagem no catálogo:
  `municipalities.weights` e `municipalities.state_weights`). O que o estudo devolve depois fica nos arquivos
  `.proximo`. O Actions não escreve a tabela no repositório (só o `catalogo.json` e a tabela de downloads do README,
  como já fazia). Nenhum deles está na lista `files` do contrato de catálogo: o leitor não precisa deles, os pesos já
  vão nos arquivos de apoio por UF.
- **Consequência do desenho: os pesos novos chegam ao app uma geração depois.** O estudo roda depois de a geração ser
  publicada, e os arquivos de apoio por UF dela já saíram com a tabela de que ela partiu. Os pesos que o lote de hoje
  calcula entram nos arquivos de apoio da geração seguinte (na semana que vem), que parte das tabelas `.proximo`. Um peso
  tem, portanto, a idade do lote que o calculou mais uma geração, e a volta nos municípios leva 10 gerações para
  calcular e 11 para chegar ao app.
- **A rotação.** A cada geração, os 600 municípios (`ANDORINHA_WEIGHTS_BATCH`) de linha mais velha e 1 estado
  (`ANDORINHA_WEIGHTS_STATES`) de linha mais velha. A ordem (`weights_tables.select_batch`): primeiro os sem linha (um
  município novo, ou um dos cinco sem amostra: custam ~30 s cada), depois os de peso calculado com outra versão do
  Valhalla, depois a data do cálculo, do mais velho ao mais novo; o desempate é o código (a sigla, nos estados). Sem
  sorteio: a mesma tabela dá o mesmo lote. A semente tem todas as linhas do mesmo dia, então o primeiro lote começa
  pelo código mais baixo (RO) e os seguintes seguem a ordem dos códigos.
- **A volta.** 5.570 municípios em lotes de 600 levam **10 gerações (10 semanas)** para dar a volta, e os 27 estados,
  27 semanas. Cada município é refeito, no máximo, 10 gerações depois da última vez; como ficam 12 gerações nas
  releases, a idade máxima de um peso cabe na janela das releases que ainda existem.
- **Orçamento de tempo.** `ANDORINHA_WEIGHTS_MINUTES` (300 no workflow, para um job de 350 min): aos 70% o lote dos
  municípios pára de começar municípios novos (os que já começaram terminam) e o estado tem o resto. O que não deu tempo
  fica para a geração seguinte, porque a rotação pega os de linha mais velha. Um estado que não termina no tempo, ou
  falha, não entra (a linha dele não muda).
- **Resiliência.** Município que dá erro vira "sem peso" e a linha antiga fica; processo que morre (o Valhalla caiu)
  encerra o lote e guarda o que já terminou; nada refeito = o `study` sai com 1 e não grava tabela.

### A conferência que substitui a trava de sha256

Antes, o gerador só aceitava a cópia do repositório, pelo sha256 do `config.py`. Isso não serve mais: a tabela muda a
cada geração, pelas mãos do gerador. A conferência equivalente (`andorinha/weights_tables.py`):

1. **De onde a geração parte** (`weights_tables.previous_tables`). O `catalogo.json` publicado diz a release da geração
   anterior. Dela, nesta ordem:
   - **as tabelas `.proximo`**, se existem e passam em tudo: o `pesos_proximo.json` é da mesma geração (`build_id`) e do
     mesmo Valhalla, o `sha256` e a contagem de cada tabela batem com o json, o formato é o certo (cabeçalho, colunas, pesos
     de 0 a 1 com uma casa e `use_trails` até o teto, geração, versão e data), um município por linha, todos na malha
     municipal, uma linha para cada UF e **a tabela não perde linhas** (no mínimo as 5.564 da semente). Release sem elas
     (o estudo não rodou ou falhou) passa ao próximo sem aviso; com elas e sem servirem, passa com aviso;
   - **as tabelas normais** da release, de que a geração anterior partiu, conferidas pelo `sha256` e pela contagem do
     catálogo e pelas mesmas verificações de formato. Se o catálogo diz as tabelas da semente, nada é baixado;
   - **a semente do repositório** (as cópias `pesos_moto_*.csv`, conferidas pelo `sha256` e pela contagem do
     `config.py`). Com aviso no log e nas `notes` do `geracao.json` quando a release devia ter as tabelas e elas não
     serviram (rede, `sha256`, formato); sem aviso na primeira geração, cujo catálogo anterior é de antes dos pesos.
   Nunca pára a geração (o custo é perder o progresso dos lotes; a rotação os refaz).
2. **O estudo** parte das tabelas normais da release que acabou de baixar (as de que a geração partiu), conferidas pelo
   `sha256` do catálogo, e escolhe o lote nelas.
3. **Publicação.** O `publish.py` confere que as duas tabelas normais existem em `data/dist/` e que o `sha256` de cada
   uma é o do catálogo; o catálogo e as tabelas saem juntos. O `sha256` que o catálogo publica (`municipalities.weights`)
   é o da tabela que sobe na release.

### Conferência com a tabela de 2026-11-14

Os mesmos pesos? Sobre os tiles da geração `2026-11-09-c17dff60` (os mesmos dos estudos), em 2026-11-15, 4 processos:

- 5 municípios (3102001, 3100203, 3100708, 3102902 e 3146750, que cobrem 0,8/0,4, 0,4/0,4, 0,5/0,3, 0,9/0,4 e
  0,6/0,2): **5 de 5 iguais**, com o mesmo centro e as 1.024 viagens contadas em todos;
- 40 municípios (38 sorteados no país e os dois maiores, São Paulo e Altamira): **40 de 40 iguais**;
- 36 do primeiro lote (os 6 sem linha e 30 sorteados entre os demais dos 600 que a rotação pega primeiro, RO, AC, AM, RR,
  PA, AP e TO): **30 de 30 iguais**; dos 6 sem linha, cinco seguem sem amostra e Noronha, que a tabela não tinha, entra
  com 0,6/0,4.

A diferença possível entre o estudo antigo e o novo é a que o teto explica: o antigo exigia rota nas 16 combinações
(`use_trails` até 0,8) e o novo, nas 8 de até 0,4, e o limite de distância da moto passou de 500 km para 5.000 km (a
config de quem usa os pacotes). Um par que tenha rota só com `use_trails` baixo conta a mais, e um município maior
que 500 km de ponta a ponta ganha pares que antes caíam: uma viagem em mil muda a média, e só decide quando as
combinações estão a menos que isso uma da outra. Nas 75 comparações não apareceu diferença.

### Tempo e a estimativa de um lote de 600

Medido em 2026-11-15 numa máquina de 20 threads (12ª geração de Intel, até 4,9 GHz), 4 processos, com outras cargas
na máquina:

| Execução | Tempo | CPU |
|---|---|---|
| 5 municípios de MG (todos médios) | 26,8 s | 83 s (16,6 s por município) |
| 40 municípios (38 sorteados no país, mais São Paulo e Altamira) | 7,8 min | 1.152 s; mediana de 17 s por município, 19 s de média sem os dois maiores; São Paulo 288 s, Altamira 142 s |
| 36 do primeiro lote (6 sem peso, 30 sorteados) | 3,4 min | 739 s; 18,7 s por município com peso; 32 s por município sem amostra (50.000 sorteios) |
| um estado, por viagem (de 400 a 580 viagens de GO, SP, PA e AM) | | 0,12 a 0,28 s por viagem (SP é a mais cara) |

Um estado inteiro são ~10.000 viagens: 0,35 a 0,8 CPU-horas. Um lote de 600 municípios: 600 x 19 s, mais ~6 municípios
grandes (~150 s) e os 5 sem amostra (~30 s), dá **~12.500 CPU-segundos (3,5 CPU-horas)**, e com o estado, 4,3.

No runner do GitHub (`ubuntu-24.04`, 4 vCPUs de um repositório público), as premissas: cada vCPU rende 50% a 65% de
uma thread da máquina da medição (processador de ~3,2 GHz no máximo, e os 4 vCPUs são 2 núcleos com hyperthreading),
os 4 processos usam os 4 vCPUs o tempo todo, e o resto (baixar os pacotes da release (~1,5 GB), extrair, abrir os
`Actor`s) leva 10 a 15 min. Então: 4,3 CPU-horas / (4 x 0,5 a 0,65) = **1,7 a 2,2 h de estudo, mais ~15 min de
preparo: de 2 a 2,5 h**, contra o limite de 6 h do job (e o orçamento do estudo, 5 h). Folga de ~2,4x no orçamento. Se o runner for
mais lento que o previsto, o orçamento de tempo corta o lote em vez de derrubar o job. A primeira execução no Actions
confirma o número: o `estudo.json` do artefato de diagnóstico `pesos` traz os segundos de cada município, e o `pesos-tempo.txt`, o
`/usr/bin/time -v` do job (pico de memória: ~0,4 GB por processo na máquina local, ~1,6 GB com 4).

Esta etapa pode ser rodada à mão, sem o Actions:

```
python scripts/weights.py study --tiles /pasta/dos/tiles --build-id 2026-11-09-c17dff60 --out /pasta/de/saida \
    --municipalities 3102001,3100203 --states 0 --workers 4
```

Sem `--municipalities` e sem `--release`, ele faz o lote da rotação sobre a semente do repositório. O `--out` recebe as
tabelas `.proximo`, o `pesos_proximo.json` e o `estudo.json` (o que foi escolhido, o que terminou e o peso, o centro, as viagens e os segundos de cada
unidade).

### Pesos por estado

`pesos_moto_estados.csv` tem uma linha por UF, com o peso para a viagem que tem **todos os pontos no mesmo estado, em
municípios diferentes** (no mesmo município vale o peso do município; entre estados, o leitor usa o padrão dele).
Colunas: `codigo_ibge` (2 dígitos), `uf`, `use_highways`, `use_trails` e as mesmas três de origem (`geracao_tiles`,
`versao_valhalla`, `data_calculo`). O peso vai no cabeçalho do arquivo de apoio da UF (`andorinha.state_weights`) e
no catálogo (`municipalities.states.<UF>.state_weights`). A geração confere o `sha256` (da release, ver acima) e que há
uma linha, e só uma, para cada UF da malha.

Um estado por geração, o de linha mais velha, na mesma rotação (27 semanas para a volta). O método, o mesmo da semente
(2026-11-14), em `weights_study.study_state`:

1. **Amostra.** 100 destinos e 100 origens uniformes no polígono da UF (malha das UFs do IBGE), cada ponto a até 150 m
   de uma via (semente `20261124` mais a sigla). Se as viagens com rota em toda a grade não chegam a 10.000, entram mais
   origens, de 10 em 10, até chegar (no máximo 1.000 origens).
2. **Grade e refino.** A mesma dos municípios (8 combinações, o centro e as vizinhas a 0,1, `use_trails` até 0,4).
3. **Escolha.** Vence o menor tempo médio; no empate, fica o centro do refino.
4. **Config.** `service_limits.motorcycle.max_distance` em 5.000 km, como em `required_config`: num estado grande, a
   viagem passa do limite padrão do Valhalla.

As rotas rodam nos mesmos processos do lote de municípios; o sorteio, no processo principal. A semente de 2026-11-14
saiu com `use_trails` 0,4 em todas as UFs e `use_highways` de 0,4 (AP) a 0,9 (DF, MT, PR, RS e TO).

**Uma tabela mais velha que os tiles não para a geração.** Mesmo com o estudo em lotes, os pesos de quase todos os
municípios são de tiles de uma geração anterior (até 10 semanas atrás, e os estados, até 27). A geração segue, o catálogo
diz de qual geração são os pesos de cada UF (`generations`, e `same_generation` falso; o mesmo dentro de
`state_weights`) e as notas, uma para os municípios e uma para os estados, vão para as `notes` do `geracao.json` (e
para o log como `nota:`, sem `AVISO`: com o desenho de entregar os pesos uma geração depois, isso é o normal, e um
aviso fixo em toda geração é ruído; o que de fato quebra, como o estudo parar, tem a issue própria). Os pesos de uma geração só se refazem com os tiles dela, então um peso tem sempre a idade do lote em que
foi calculado.

Tamanhos dos arquivos de apoio, medidos em 2026-11-14, já com o peso do estado (kB compactados): AC 17, AL 14, AM 115,
AP 22, BA 139, CE 51, DF 1, ES 24, GO 116, MA 76, MG 285, MS 60, MT 135, PA 128, PB 35, PE 36, PI 47, PR 114, RJ 24,
RN 21, RO 37, RR 24, RS 137, SC 70, SE 13, SP 158, TO 74; 1,97 MB no país (11,9 MB abertos). Conferir as duas cópias leva 0,4 s e gravar os
27 arquivos, 4 s. Conferido no mesmo dia com os tiles da geração `2026-11-09-c17dff60`: montados de novo com os
arquivos de apoio ao lado, os pacotes da base e do DF saem com o mesmo sha256 dos publicados (os pacotes de tiles não
mudam), e o arquivo do DF abre com o zstd e dá o peso de Brasília (0,8/0,4).

## Travas de publicação

O `scripts/publish.py` não publica se:
- a geração ainda roda ou caiu no meio (marca `GERACAO_EM_ANDAMENTO` em `data/dist/`),
- o catálogo foi gerado com outra versão do Valhalla,
- algum pacote falta, não bate o sha256 ou passa do limite do GitHub Releases,
- algum arquivo de apoio dos municípios falta, não bate o sha256, ou sumiu de um catálogo que já os tinha,
- alguma das duas tabelas de pesos (`pesos_moto_municipios.csv` e `pesos_moto_estados.csv`, que sobem na release e de
  que a geração seguinte parte) falta ou não bate o sha256 do catálogo,
- algum pacote perdeu mais de 5% dos tiles em relação ao `catalogo.json` publicado, ou mais de 10% quando o
  extrato vem de outra fonte que o da publicada (os recortes do openstreetmap.fr e da Geofabrik diferem na
  fronteira: o AC perde 6,4% na Geofabrik, só de vias do Peru e da Bolívia) (`--accept-drop` para passar),
- a geração usa um OpenStreetMap mais antigo que o da publicada (`--accept-older` para passar),
- já existe uma release PUBLICADA com o mesmo `build_id`,
- com `--commit`, o git do repositório não está com o e-mail anônimo do GitHub (`…@users.noreply.github.com`).

A release sobe como rascunho, sem arquivos; cada arquivo que ainda não está lá (ou está com outro tamanho) sobe — os
pacotes, os arquivos de apoio dos municípios, o `LICENCA-DADOS.txt` e o `catalogo.json` da geração, que sobe sempre de novo numa retomada (refeita com o
mesmo `build_id`, a geração tem outro `built_at` no mesmo tamanho); só com todos presentes e do tamanho certo ela é
publicada. Se a subida cair no meio, fica um rascunho que ninguém vê, e
rodar o mesmo comando de novo retoma do que falta. Só depois disso o `catalogo.json` e a tabela "Baixar por estado"
do README da raiz (gerada a partir do catálogo, com o link de cada pacote) mudam, no mesmo commit: o catálogo publicado
nunca aponta para arquivos que ainda não existem. Um erro do `gh` mostra o comando e a mensagem dele; sem rede ou sem
login não é confundido com "a release não existe". `--commit` sem `--release` e `--push` sem `--commit` dão erro.

## Convenções

**Idioma**

- Nomes de arquivos e pastas de dados e documentos: português, sem acento, sem espaço (`andorinha-GO.tar.zst`,
  `catalogo.json`, `geracao.json`, `pacotes.json`, `PENDENCIAS.md`, `gerar.yml`). Exceção: os nomes que o
  ecossistema espera (`README.md`, `LICENSE`, `requirements.in` e `requirements.txt`; `requirements-testes` segue o
  padrão do ecossistema com o sufixo em português).
- Código: nomes de variáveis, funções, classes, módulos e arquivos de código em inglês (`packing.py`,
  `publish_release`, `RunRecorder`); siglas como são (`osm`, `ibge`). Opções de linha de comando (`--from`,
  `--accept-drop`), variáveis de ambiente (`ANDORINHA_THREADS`), nomes de etapas (`packages`, `validate`) e
  identificadores do workflow (entrada `publish`, job `generate`) também.
- Formato dos dados: chaves do `catalogo.json` e do `geracao.json` e valores com significado (`ok`, `failed`) em
  inglês.
- Comentários, docstrings, documentação, mensagens de log e de erro, notas das releases, nomes dos passos do workflow
  e mensagens de commit: português.

**Versão do formato (`schema`)**

O `catalogo.json` tem o campo `schema`, a versão do formato. Ela sobe quando muda algo que um leitor feito para a
versão anterior não entenderia: uma chave renomeada ou removida, um nome ou caminho de arquivo que muda, um valor que
muda de significado. Não sobe quando só entram dados novos, ou chaves novas que um leitor antigo pode ignorar. Numa
mudança que exige subir a versão, primeiro o gerador passa a escrever o formato novo junto com o antigo, depois quem
lê passa a usar o novo, e só então o antigo sai e a versão sobe.

## Licença

Código: MIT (`LICENSE` desta pasta). Os pacotes gerados são base derivada do OpenStreetMap, sob a ODbL 1.0 (ver o
README da raiz).
