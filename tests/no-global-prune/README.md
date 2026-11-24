# Captain 1.16.1 — candidato local sem prune global automático

## Estado e limites

Preparado e exercitado localmente, sem commit, push, CI hospedado, publicação GHCR, deploy ou alteração de pin. Não executar o workflow manual sem autorização de publicação. Esta documentação não autoriza produção.

- Base upstream: `8faf50096549166ae2fdadb61f54af8a130de6f1` (`v1.16.1`).
- Branch local: `fix/nuclito-captain-1.16.1-no-global-prune`.
- Base oficial linux/amd64: `caprover/caprover@sha256:7bbe8ba293d591882dff303c79e460b641f4218a8f8a9cf6658fd4d66f9543c1`.
- Base image/config ID: `sha256:846c0f4db30d4e678e2bcb59200cb2e1ec9f0e2e8e7b8776689ef7be413b90f3`.
- Imagem local testada: `nuclito-caprover:1.16.1-prepared-6a1ec2ed7716`.
- ID imutável LOCAL: `sha256:879e6f910f82a329153738e7118231ac2cd23e4bbf7522718d746babb96a46ab`. Não é um digest de manifest GHCR nem evidência de publicação.

## Delta e processo reaproveitado

A customização de 1.15.5 foi portada para a base nova, sem cherry-pick cego do código antigo. A única alteração em `src/` é remover 12 linhas de `src/docker/DockerApi.ts`: a continuação que dispara prune global após remoção de container específico e o timer de prune após update de serviço. O helper explícito e o transporte de prune manual permanecem intactos. Não foram alterados lockfile, dependências, probe Docker v1.44, frontend, providers ou configurações de apps.

`source-contract.cjs` exige transformação exata contra o commit upstream e percorre AST de todos os TypeScript de produção. Compilar a base exata e o candidato só pode mudar `docker/DockerApi.js` e seu `.map`. O Dockerfile herda a release oficial por digest e copia somente esses dois arquivos. Entrypoint/Cmd/Env/runtime e UI são herdados da imagem oficial nova.

O caminho existente `.github/workflows/publish-prune-fix-ghcr.yml` foi adaptado, não registrado/despachado: somente workflow_dispatch, repo/ref exatos, Node 24.22.1, base source/image fixados, actions por SHA, publicação apenas após testes e contrato da imagem. A tag futura inclui o SHA completo do commit e deve estar ausente no GHCR. Nenhum hook de deploy foi acrescentado. Os outros workflows upstream continuam sem alterações; não publicar na branch master/release nem abrir PR para elas por reflexo.

## Provas reais locais

- Ferramentas: Node 24.22.1/npm 11.19.1 da imagem oficial nova, não o Node 22 do host.
- Dependências instaladas com `npm ci --ignore-scripts --include=dev`, sem mudar package.json/package-lock.json.
- RED no upstream intacto: 46 testes, 44 pass e 2 falhas comportamentais esperadas, uma por caminho automático de prune.
- GREEN no candidato: 46/46, zero falhas/skips; transportes Docker sintéticos e guard de rede antes da importação real.
- Lint e Prettier do único arquivo de produção alterado: pass.
- Build canônico da base e candidato: pass; comparação de TODOS os outputs compilados: só os dois arquivos previstos diferem.
- Contratos sintéticos de archive/imagem/registry/admissão: 24 métodos unittest, pass. Os casos de status HTTP são sintéticos; não houve acesso/push GHCR nesse teste.
- Overlay Docker local construído de contexto fechado com três arquivos. Export completo de containers base/candidato NUNCA iniciados: somente os dois paths compilados diferem; 23 entradas UI idênticas e Config inteira igual. Guards de nomes, hardlinks, metadados e PAX/GNU foram preservados. O contrato lê TAR completo achatado de docker export, não layers OCI/docker save, e não extrai paths do TAR no host.
- npm audit do lockfile: produção zero achados; completo 26 moderados, zero altos/críticos. São achados herdados de desenvolvimento; não aplicar npm audit fix nem alterar dependências fora deste delta. Não é uma varredura CVE de todos os pacotes da imagem/SO.

A suíte upstream completa não foi executada neste host: contém testes que usam Docker/caminhos globais. Os testes locais não certificam inicialização do Captain, reconciliação real Nginx/Certbot, TLS ou fluxos de negócio; esses pertencem à qualificação operacional futura.

Os containers de ferramenta usam apenas o workspace isolado, sem socket Docker, sem volumes de produção, entrypoint shell explícito, sem privilégios/capabilities, rootfs RO, 2 CPUs/4 GiB, swap desabilitado e PIDs limitados. Rede habilitada somente para npm ci/audit; testes/build de código sem rede. Containers de exportação nunca iniciados; todos removidos por identidade com ausência relida.

## Ressalvas operacionais

Esta correção não desativa `DiskCleanupManager` nem outras remoções seletivas/configurações existentes. Não equivale a uma garantia de ausência de toda limpeza.

O Nginx de produção está fixado explicitamente em override persistente. Atualizar Captain não troca esse pin automaticamente: qualificar imagem corrigida por digest e obter autorização própria antes de atualizar override. Startup Captain pode reconciliar Nginx/Certbot, substituir tasks, renovar/recarregar TLS. Não prometer que apenas Captain reiniciará.

Rollback futuro deve manter a imagem customizada atualmente implantada e a configuração original exata; não usar a imagem oficial como atalho, pois reintroduziria autoprune. Capturar snapshots cifrados frescos, configurações/Specs/TLS necessários e estado de apps antes da manutenção. Nenhum snapshot antigo foi renovado ou tratado como autorização.

## Artefatos

Evidências locais: `../proof/` relativo à raiz do checkout; usar o caminho absoluto do relatório ao reproduzir verificações. Raiz operacional autorizada: `/home/nuclia/.hermes/profiles/nucleo-infra/workspace/caprover-1.16.1-preparation/`.

Recibos relevantes na pasta proof: jest-red.json, jest-green.json, source-contract.json, compiled-contract.json, audit-production.json, audit-all.json, artifact-contract-tests.log, image-build-execution.json, image-contract.json e logs de fases. O primeiro recibo install-execution.json marcou container_removed=false por comparar mensagem Docker com maiúsculas; o inspect independente confirmou ausência e o comparador do wrapper foi corrigido para case-insensitive. Não interpretar esse primeiro wrapper exit 1 como falha de npm ci, nem reescrever o recibo histórico.
