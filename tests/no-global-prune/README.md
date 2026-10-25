# Captain 1.15.5 — remoção local do prune global automático

**Estado:** candidato LOCAL, sem commit/stage/push, sem imagem construída/publicada,
sem rollout. Revisão independente e gates hospedados continuam pendentes.

## Identidades e escopo

- Base upstream exata: `5a5c89c67ea819faf2032a77649eaf84c106f7da` (1.15.5).
- Branch nova: `fix/nuclito-captain-1.15.5-no-global-prune`.
- Runtime herdado: `caprover/caprover@sha256:585a559a6d6251350f38175b53a00aa0a54a14984dffb6bf74df962da5c029e3`.
- Única alteração de produção: 12 linhas removidas de `src/docker/DockerApi.ts`.
  Removidos o timer pós-update e o prune depois de remover um container específico.
  O helper explícito `pruneContainers()` permanece byte a byte igual.
- Nenhuma mudança de frontend, API pin, providers, app definitions, journal,
  workers, ZIP, configuração de limpeza, cron ou infraestrutura.

## Fechamento dos caminhos

`source-contract.cjs` analisa AST de TODOS os arquivos `src/**/*.ts`, registra
referências e exige a transformação exata da base: somente duas continuations
removidas. Resta uma referência ao transporte `dockerode.pruneContainers`, dentro
do próprio helper explícito, e **zero chamadas automáticas a esse helper**.

O segundo caminho pertence a `CaptainManager.updateNetDataInfo` e
`CaptainManager.updateGoAccessInfo` (inclusive inicialização), não apenas a um
cleanup de build. Stop/wait/remove do container nomeado, disconnect da rede,
304/404 e propagação de erros continuam presentes.

`ImageMaker -> buildImageFromDockerFile -> dockerode.buildImage` não chama prune;
as opções t/buildargs/version/registryconfig e o AutoRemove de containers
não-sticky não foram alterados. Testes adicionais exercitam o método real de
build com stream/transporte sintéticos, sem construir imagem.

**Limite importante:** `DiskCleanupManager.resetScheduledTasks` ainda pode
agendar seleção de imagens não usadas e `deleteImages` por ID conforme a
configuração existente. Não há identificador `scheduleDiskCleanup` nesta base.
Essa limpeza de imagens, outras remoções específicas e scripts de desenvolvimento
não foram desligados nem certificados como seguros para produção. A correção não
significa “nenhum efeito global de manutenção”.

## Reprodução local segura

Node observado `22.23.2`, npm `12.0.2`; lockfile intacto.
Instalação autorizada: `npm ci --ignore-scripts` em checkout isolado, cache privado.
Não executar Docker, scripts dev/clean nem a suíte upstream inteira neste host.

```sh
node tests/no-global-prune/source-contract.cjs
node node_modules/eslint/bin/eslint.js src/docker/DockerApi.ts
node node_modules/prettier/bin/prettier.cjs --check src/docker/DockerApi.ts
node node_modules/jest/bin/jest.js --config tests/no-global-prune/jest.config.cjs --runInBand
python3 tests/no-global-prune/artifact-contract-test.py
npm run build
```

O setup bloqueia conexões nativas TCP/Unix, HTTP(S), TLS, DNS, UDP e fetch ANTES
de importar a classe real versionada. Dockerode é mockado antes da importação,
inclusive o probe de versão no escopo do módulo. Os dois testes novos também
importam esse guard diretamente para proteger sua invocação isolada. Não confundir
o log sintético `Docker API Version on host: 1.55` com observação de daemon real.

Evidências locais: RED comportamental original com 2 falhas (cada caminho fazia
1 prune quando se exigia 0), GREEN inicial 13/13, suíte segura final 46/46, mais
8 testes puros dos contratos de artefato/admissão/registro. Baseline seguro:
33/33 (31 preexistentes + 2 provas do build já correto). Nenhuma suíte tem skips.
O import defensivo foi acrescentado depois do primeiro RED/GREEN, sem alterar
fixtures/assertions; o harness final foi repetido na base exata e reproduziu as
mesmas duas falhas. Os recibos originais e finais estão preservados no scratch.
O defeito de log pós-Jest não apareceu sob esses mocks; não se afirma suíte
upstream completa verde. npm ci reportou 40 vulnerabilidades herdadas; sem upgrade.

A comparação de TODOS os outputs compilados entre base e candidato admite apenas
`docker/DockerApi.js` e `.js.map`; o build completo e o lint de escopo passaram.

## CI futuro, publicação e dashboard

É adaptação da implementação já existente em `voltdatalab/caprover`, não criação
de outro repositório: reaproveita organização, conceito de overlay e path do
workflow. O histórico `test/no-post-update-prune`/`4609f1be515a9376e021316caa1417c9f52305f6`
é baseado em 1.14.2 e removia apenas o timer, além do probe v1.38→v1.44.
Nesta base 1.15.5 o probe já é v1.44 (preservado); removem-se os DOIS caminhos
automáticos, inclusive cleanup no startup. Não se reutiliza a imagem antiga
1.14.2 nem se confunde seu CI histórico com validação deste candidato.

Por instrução posterior do parent, utiliza-se o PATH já registrado
`.github/workflows/publish-prune-fix-ghcr.yml` (ID `313740864`) APENAS na branch
nova. Não se criou um workflow concorrente em `nuclito-captain-1.15.5-no-prune.yml`,
nem se alterou a branch antiga/default para registrar um novo path.
O workflow tem nome próprio de 1.15.5, somente `workflow_dispatch`, guard shell
exato para repositório/ref, ações fixadas a SHAs obtidos de tags públicas e somente
`contents: read` / `packages: write`. O parent poderá despachar o ID registrado
na ref nova depois de revisão/commit/publicação da branch; isso NÃO foi executado.

Tag futura: `ghcr.io/voltdatalab/caprover:1.15.5-no-global-prune-<SHA completo>`.
Recusa sobrescrever tag existente; falhas de registro diferentes de 404 bloqueiam.
Sem secrets de produção, environments, hooks de deploy, CLI, SSH ou Portainer.

O Dockerfile final usa a base oficial por digest e copia SOMENTE os dois arquivos
compilados de DockerApi. Cmd, entrypoint, dependências e dashboard são herdados.
Em runner CI isolado, o workflow testa/compila antes de construir linux/amd64,
exporta containers NUNCA iniciados e exige filesystem idêntico fora desses dois
arquivos, dashboard/index e todos os assets idênticos, Config runtime idêntico,
e backend oficial compatível com a compilação da base exata. Arquiva hashes,
inventários, logs e provas antes/depois do push; verifica o config digest remoto
contra o da imagem testada. Os testes locais desse verificador usam archives
sintéticos explicitamente identificados: **não certificam uma imagem real**.

## Correção LOCAL do contrato de imagem (QUALITY-01/02)

Este derivado de revisão usa a branch local
`fix/nuclito-captain-1.15.5-image-contract-fix`, sobre a mesma base. O candidato
anterior permanece congelado. Produção, Dockerfile e workflow são byte-idênticos
ao candidato anterior; o guard de dispatch ainda exige a branch original acima.
Não despachar esta branch de revisão. O comando Python já existente executa os
8 testes históricos intactos e as novas regressões no mesmo arquivo.

O contrato é o TAR **achatado e completo de `docker export`**, de containers
nunca iniciados; não `docker save`, OCI layers nem aplicação de layers/whiteouts.
Inventaria arquivos regulares (hash/tamanho), diretórios inclusive vazios,
symlinks, hardlinks, FIFOs e dispositivos de bloco/caractere. Compara tipo,
mode, uid/gid, uname/gname, link target, tamanho, major/minor e metadados PAX
não temporais (incluindo xattrs); tipos desconhecidos e sparse são recusados.
Somente mtime/atime/ctime são omitidos da comparação: os tempos de cópia e de
ancestrais podem mudar no overlay sem alteração de conteúdo/permissões.
`path`/`linkpath` PAX não são descartados semanticamente: seus valores lógicos
são usados como nome e alvo; apenas a duplicação desses campos no mapa PAX é
omitida. Config runtime inteira e UI continuam sob comparação exata.

Nomes de membros são relativos ao rootfs: um único prefixo opcional `./`, uma
barra final opcional somente para diretórios; `.` e `./` representam o diretório
raiz explícito e são inventariados como `.`. Recusam-se absolutos, componentes
vazios, `.`/`..` internos, whiteouts `.wh.*` e qualquer duplicata canônica,
inclusive diretórios, aliases PAX e conflitos de tipo. A grafia lógica é lida
antes da remoção de barras feita por algumas versões de tarfile, com suporte a
USTAR/PAX/GNU longname; não se extrai o TAR no filesystem do host.

No derivado local `fix/nuclito-captain-1.15.5-pax-gnu-fix` (rodada final 3/3),
`entry.name` efetivo do próprio tarfile é sempre validado primeiro e continua a
única autoridade de nome; não se escolhe uma precedência PAX/GNU alternativa.
Os GNU longnames preservados e o PAX path devem concordar com esse nome.
Somente diretórios admitem equivalência canônica de `./` e barra final única,
inclusive raiz `.`/`./`; arquivos exigem igualdade literal. O nome físico USTAR
pode ser placeholder/truncado quando substituído por extensão legítima; sem
extensão sua grafia bruta é validada para não perder barras inválidas removidas
pelo parser. Conflitos GNU/PAX são recusados nas duas ordens, mesmo entre nomes
seguros distintos. As novas regressões executam `main()` com TAR e provas em RAM,
registrando `TarInfo.name` e PAX path sem extrair nem usar nomes como paths do host.
Os 21 métodos/56 subtests anteriores permanecem intactos; CI/imagem/revisão
independente continuam pendentes. Workflow, Dockerfile e regras de links intactos.

Alvos de links têm política distinta dos nomes. Symlinks absolutos, relativos
com `..`, dangling ou cíclicos são dados válidos preservados literalmente
(ex.: `/etc/alternatives`); não são seguidos. Hardlinks são referências à raiz
do archive, inclusive targets absolutos ou `.`/`..` que permaneçam dentro dela.
Exigem target presente no export completo, não diretório, e resolução finita
sem ciclos. Aceitam encadeamento e referências adiante, inclusive hardlink para
symlink sem dereferenciá-lo; nenhuma leitura de caminho do host é feita por link.
O inventário do hardlink inclui também o registro do referent final: uma alteração
no compilado permitido não pode esconder alteração de conteúdo em um terceiro
path hardlinked. Targets de symlink vazios ou com NUL são recusados.

Os testes sintéticos exercitam `main()` e o inventário, incluindo os três casos
de diretórios do revisor e duplicata de arquivo com conteúdo idêntico. Recibos
RED/GREEN, contagens nativas e preservação estão no handoff deste derivado.
Não são evidência de build de imagem, execução hospedada ou rollout; limites
de recursos globais do TAR não são um sandbox e continuam fora desta correção.

## Rollback e operação

Rollback de identidade previsto: o digest oficial acima. Não é rollback prune-free:
a imagem antiga pode voltar a executar prune inclusive durante startup. A rota
nativa versionInfo/latestVersion não aceita este GHCR arbitrário e usa o fluxo
antigo; não foi operada. Não há helper de swap operacional comprovado nesta tarefa.
A troca futura depende de imagem verificada e execução delimitada do operador,
com snapshots, readiness e rollback específicos. `captain-certbot` paused
preexistente impede declarar quiet window; não foi corrigido.
