# Diagnóstico

## Critério de ordenação
Os itens estão ordenados do maior para o menor impacto operacional e financeiro:
1. Distorção de valores pagos e saldos financeiros.
2. Erro no cálculo de prazo de pagamento e priorização da carteira.
3. Perda silenciosa de eventos ou divergências entre bases.
4. Fragilidades estruturais de banco, processo operacional e documentação.

---

## 1. Evento `ev-001` repetido com valor e `atualizado_em` alterados
- **Evidência:** `pagamentos.json`, `ev-001` aparece duas vezes: a primeira com `"valor": 15000.0` e `"atualizado_em": "2025-03-10T10:00:00-03:00"`, e a segunda com `"valor": 15500.0` e `"atualizado_em": "2025-03-11T09:00:00-03:00"`.
- **Impacto:** Sem deduplicação por `evento_id` priorizando a atualização mais recente, o total pago do processo sobe para 30500 em vez de 15500.
- **Tipo:** Causa.

## 2. Evento `ev-002` duplicado e fora do formato documentado
- **Evidência:** `pagamentos.json`, `ev-002` surge duas vezes de forma idêntica, com `"valor": "2.500,50"` (string no padrão BR) e `"data_pagamento": "10/04/2025"` (`DD/MM/AAAA`, diferente de `AAAA-MM-DD` em `DOCUMENTACAO_TRIBUNAL.md`).
- **Impacto:** Somar ambas as ocorrências duplica o valor, e parsers que esperam número/data no padrão documentado podem falhar e perder o lançamento.
- **Tipo:** Causa (duplicidade e formatação fora do contrato) e consequência (falha de validação).

## 3. Registro `ev-007` com `tipo: "estorno"`
- **Evidência:** `pagamentos.json`, `ev-007` traz `"tipo": "estorno"` para o processo `0034567-32.2018.8.26.0405` (um dia após o `ev-006` de 5000). Contraria `DOCUMENTACAO_TRIBUNAL.md` (que diz ser sempre `"pagamento"`), embora o `schema.sql` aceite `'estorno'`.
- **Impacto:** Se o processamento não diferenciar `"pagamento"` e `"estorno"` no cálculo do saldo, o estorno pode ser somado como pagamento indevido, inflando o total pago do processo para 10000 em vez de 0.
- **Tipo:** Causa.

## 4. Evento `ev-009` com valor negativo
- **Evidência:** `pagamentos.json`, `ev-009` registra `"valor": -200` com `"tipo": "pagamento"`.
- **Impacto:** Viola `CHECK (valor > 0)` em `schema.sql` e tende a falhar na carga ou ser ignorado sem aviso conforme o passo 5 do fluxo operacional.
- **Tipo:** Causa.

## 5. Valor de face divergente no processo `0023456-21.2020.8.26.0053`
- **Evidência:** `30.000,00` no `tribunal.csv` versus `31000.00` no `hub.csv`.
- **Impacto:** Distorce o saldo a pagar e a conciliação entre a base do tribunal e o sistema.
- **Tipo:** Causa (a confirmar).

## 6. Prazo de pagamento inconsistente no processo `0012345-10.2019.8.26.0053`
- **Evidência:** `apresentacoes.json` indica `"apresentado_em": "2025-04-02"`, mas o `hub.csv` traz `prazo_pagamento=2027-12-31`.
- **Impacto:** Pela regra do `ENUNCIADO.md`, apresentações até 2 de abril vencem em 31 de dezembro do ano seguinte (`2026-12-31`), não em `2027-12-31`, afetando a priorização da carteira.
- **Tipo:** Causa.

## 7. Ambiguidade de fuso horário e prazos divergentes em apresentações de 02/04/2025
- **Evidência:** Em `apresentacoes.json` e `hub.csv`:
  - `0012345-10.2019.8.26.0053`: `"2025-04-02"` -> `prazo_pagamento=2027-12-31`.
  - `0045678-43.2021.5.02.0011`: `"2025-04-02T23:30:00-03:00"` -> `prazo_pagamento=2026-12-31`.
  - `0056789-54.2017.4.03.6100`: `"2025-04-03T01:00:00Z"` (02/04 às 22h em `-03:00`) -> `prazo_pagamento=2027-12-31`.
- **Impacto:** Mesma data civil no horário de Brasília produz prazos diferentes no `hub.csv`, sem regra única que explique todos os casos.
- **Tipo:** Consequência da ausência de definição de fuso em `DOCUMENTACAO_TRIBUNAL.md` (e causa de divergência no cálculo do prazo).

## 8. Evento `ev-004` com `valor: null`
- **Evidência:** `pagamentos.json`, `ev-004` com `"valor": null`.
- **Impacto:** Viola `valor NUMERIC(15,2) NOT NULL` em `schema.sql`, gerando falha de importação e perda silenciosa.
- **Tipo:** Causa.

## 9. Evento `ev-010` com `processo` vazio
- **Evidência:** `pagamentos.json`, `ev-010` com `"processo": ""`.
- **Impacto:** Impede associar o lançamento a um precatório, resultando em perda silenciosa.
- **Tipo:** Causa.

## 10. Convivência de formatos com e sem máscara para o mesmo processo
- **Evidência:** O processo `0023456-21.2020.8.26.0053` aparece mascarado (25 caracteres, como em `DOCUMENTACAO_TRIBUNAL.md`) em `ev-003` e `ev-004`, e sem máscara (20 dígitos) em `ev-005` e no `hub.csv`.
- **Impacto:** Comparações exatas deixam parte dos eventos sem correspondência e sujeitos a descarte silencioso.
- **Tipo:** Causa.

## 11. Divergências de cadastro, grafia e cobertura entre `tribunal.csv` e `hub.csv`
- **Evidência:**
  - `0045678-43.2021.5.02.0011`: `"União"` no `tribunal.csv` vs. `"Município de Santos"` no `hub.csv`.
  - `0067890-65.2016.8.26.0053`: consta no `tribunal.csv`, `apresentacoes.json` e `pagamentos.json` (`ev-011`), mas falta no `hub.csv`.
  - `0056789-54.2017.4.03.6100`: consta no `hub.csv`, `apresentacoes.json` e `pagamentos.json` (`ev-009`), mas falta no `tribunal.csv`.
  - `0012345-10.2019.8.26.0053`: `"Município de São Paulo"` no `tribunal.csv` vs. `"MUNICIPIO DE SAO PAULO"` no `hub.csv` (enquanto o próprio `hub.csv` usa `"Município de São Paulo"` em outro registro).
- **Impacto:** Gera divergência de ente devedor, precatórios e pagamentos fora de uma das bases e falsas diferenças na conferência por texto exato.
- **Tipo:** Mistura de causas (cadastro/cobertura) e consequências da conferência manual.

## 12. Data de pagamento anterior à data de apresentação no `0012345-10.2019.8.26.0053`
- **Evidência:** `pagamentos.json` (`ev-001`) traz `"data_pagamento": "2025-03-10"`, enquanto `apresentacoes.json` traz `"apresentado_em": "2025-04-02"` (mesma data exibida no `hub.csv`).
- **Impacto:** Pode indicar erro de data em um dos eventos ou inconsistência cronológica no histórico do precatório.
- **Tipo:** A confirmar com o tribunal.

## 13. `tribunal.csv` fora do leiaute especificado na documentação
- **Evidência:** Usa `;`, codificação `ISO-8859-1` e formato numérico BR (`45.000,00`), enquanto `DOCUMENTACAO_TRIBUNAL.md` indica `,`, `UTF-8` e ponto decimal.
- **Impacto:** Quebra leitores baseados na documentação e dificulta a conferência manual no Excel.
- **Tipo:** Causa (no arquivo entregue) e agravante para o passo 3 do fluxo.

## 14. Lacunas no `schema.sql`
- **Evidência:** `pagamento` não tem `UNIQUE(evento_id)` nem `atualizado_em`, `precatorio` não tem `CHECK` validando os 20 dígitos do CNJ nem colunas para `apresentado_em`, `prazo_pagamento` e total pago, apesar de o `ENUNCIADO.md` citar cálculo de prazo e total pago.
- **Impacto:** Permite duplicatas, não identifica a versão mais recente de um evento, não valida o formato do número do processo no banco e dificulta auditoria ou derivação consistente de saldos e prazos.
- **Tipo:** Causa.

## 15. Fragilidades no fluxo operacional (`ENUNCIADO.md`)
- **Evidência:** Reimportação do mesmo arquivo após falha (passo 2), conferência manual por texto exato no Excel (passo 3), ausência de alerta em falhas (passo 5) e `pagamentos.json` cobrindo fev–ago/2025 em vez de apenas os últimos 7 dias (passo 1).
- **Impacto:** Reprocessamentos duplicam valores, diferenças de formatação geram ruído e falhas passam sem notificação.
- **Tipo:** Causa.

## 16. Documentação incompleta ou divergente (`DOCUMENTACAO_TRIBUNAL.md`)
- **Evidência:** Descreve `tipo` como sempre `"pagamento"` (há `"estorno"`), não define fuso horário e não documenta `apresentacoes.json`.
- **Impacto:** Mantém premissas incorretas e ambiguidade sobre campos, fusos e fontes de dados.
- **Tipo:** Causa.

---

# Prevenção

## Processo

O arquivo recebido deve ser armazenado sem ser sobrescrito. Posteriormente é criado um hash em SHA-256 de cada arquivo para ser registrado no relatório, o que permite reprocessamento e auditoria seguros.

A conferência manual no Excel dá lugar à comparação automatizada no script, que normaliza os dados recebidos para apontar apenas divergências reais. Toda execução gera logs e um relatório com o motivo de cada ocorrência, alertando a operação sempre que houver eventos rejeitados, eliminando as perdas silenciosas.

Por fim, vale pedir ao tribunal uma documentação atualizada, definindo o formato de valores, datas e o tipo estorno.

## Banco

O SQL completo está em `prevencao.sql`. As mudanças principais:

- `pagamento` ganha `atualizado_em` e `UNIQUE (evento_id)`, a gravação vira upsert que só substitui por uma versão mais recente, então reprocessar não duplica. Antes do `UNIQUE`, os duplicados já existentes precisam ser resolvidos. Linhas novas passam a exigir `atualizado_em`, sem quebrar as antigas (`NOT VALID`).
- `precatorio` também é gravado por upsert, a partir do `precatorios.json`.
- O total deixa de ser acumulado e o prazo deixa de ser armazenado, os dois passam a ser calculados por views (`v_total_pago` e `v_prazo_pagamento`). O banco é a fonte da verdade, e os totais e o prazo do `precatorios.json` servem para reconciliação.
- `arquivo_recebido` registra o hash de cada arquivo. `evento_rejeitado` guarda só os eventos que ficaram de fora, com categoria, motivo e o acompanhamento da resolução. O que entrou com alerta fica no `relatorio.json`, que registra tudo o que aconteceu em cada execução.
- O processo passa a ter `CHECK` de 20 dígitos. Antes dele, os processos fora do formato precisam ser normalizados.

Limite conhecido: se a mesma versão de um evento chegar com outro valor em arquivos diferentes, o upsert mantém a primeira. Dentro de um arquivo, o `limpeza.py` já marca esse caso como em dúvida.

# Dúvidas

Perguntas para alinhar antes de formalizar uma proposta de limpeza definitiva. A numeração entre parênteses refere o item do diagnóstico.

## Para o tribunal

1. O campo `atualizado_em` é sempre confiável para definir a versão mais recente de um `evento_id`, ou há casos de atualização fora de ordem? (1)
2. Como o `tipo: "estorno"` deve ser tratado no saldo do processo, e existe algum campo ou regra para identificar qual pagamento ele estorna? (3)
3. Qual a origem e o significado do `ev-009` com `"valor": -200` e `"tipo": "pagamento"`? (4)
4. Qual é o `valor_face` correto para `0023456-21.2020.8.26.0053`: `30.000,00` ou `31.000,00`? (5)
5. Qual é o ente devedor correto para `0045678-43.2021.5.02.0011`: União ou Município de Santos? (11)
6. No processo `0012345-10.2019.8.26.0053`, a data de pagamento em `2025-03-10` anterior à apresentação em `2025-04-02` é esperada ou indica inconsistência de data? (12)
7. O `tribunal.csv` é um recorte parcial ou a base completa? Por que `0056789-54.2017.4.03.6100` não está nele? (11)
8. O que deve ser feito quando chegarem eventos com dados incompletos (descartar, aguardar reenvio corrigido)? (8, 9)
9. O `pagamentos.json` contém registros de um período específico? (o arquivo atual traz registros de meses anteriores) (15)

## Para a operação

1. O `tribunal.csv` analisado é o arquivo original do tribunal ou o salvo pelo Excel? A documentação indica `UTF-8`, separação por `,` e ponto decimal, mas o arquivo está em `ISO-8859-1`, com `;` e vírgula decimal. (13)
2. De onde vem o `apresentacoes.json` e qual o papel dele no fluxo? (16)
3. Qual fuso horário deve ser assumido para `apresentado_em` e datas sem horário? (7, 16)
4. Quando o script precisa rodar de novo após uma falha, ele reprocessa tudo do zero ou existe algum controle do que já foi importado? (15)
5. Onde o sistema guarda o total pago e o prazo hoje, já que o `schema.sql` não tem essas colunas, e de onde sai o `hub.csv`? (14)
6. Por que alguns processos no `hub.csv` estão com prazo diferente da regra descrita no enunciado? (6, 7)
7. Por que `0067890-65.2016.8.26.0053` não aparece no `hub.csv`? (11)
8. Quando o tribunal e o sistema divergem, qual base manda? (5, 11)
9. Em caso de falha, os itens ignorados ficam armazenados ou são completamente ignorados? (15)
10. O total pago usado na operação é líquido de estornos, ou pagamentos e estornos são acompanhados separadamente? (3)