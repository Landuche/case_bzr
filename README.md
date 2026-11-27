# Case BZR Group 

Diagnóstico, limpeza e prevenção das divergências entre o sistema e os arquivos.

## Estrutura

| Arquivo | Conteúdo |
|---|---|
| `ANALISE.md` | Diagnóstico, limpeza, prevenção e dúvidas |
| `limpeza.py` | Programa de limpeza: lê os arquivos como vieram e gera a base limpa e o relatório |
| `prevencao.sql` | Mudanças propostas no banco |
| `requirements.txt` | Dependencias do projeto |
| `tests/` | Testes das regras de limpeza |
| `dados/` | Arquivos originais do case (não são alterados) |


## Como os componentes se encaixam

O tribunal envia os arquivos, o `limpeza.py` valida, normaliza e deduplica, e a ingestão grava a saída no banco por `evento_id`, sem somar valores. O total pago e o prazo passam a ser calculados por views a partir dos eventos. O detalhamento está em `ANALISE.md` (Limpeza e Prevenção).

## Como rodar

Requer Python 3.10 ou mais recente.

```bash
python -m pip install -r requirements.txt
python limpeza.py
```

Por padrão, lê `dados/` e grava em `output/`:

- `precatorios.json`: precatórios reconciliados, com prazo e totais para conferência
- `pagamentos.json`: eventos limpos, um por `evento_id`, na versão mais recente
- `relatorio.json`: hash de cada arquivo lido e cada ocorrência (corrigido, descartado ou em dúvida), com o motivo

As pastas podem ser trocadas com `--data-dir` e `--output-dir`. O programa se recusa a gravar na pasta dos arquivos originais.

## Testes

```bash
python -m pytest -q
```

Cobrem as regras de maior risco: 
- deduplicação por versão.
- estorno.
- reprocessamento.
- valores ambíguos.
- fuso horário na data de apresentação.
- prazo na borda de 2 de abril e a garantia de que os originais não são alterados.

## SQL

O `prevencao.sql` roda sobre o `schema.sql` e foi testado em um PostgreSQL local.
As gravações estão como `PREPARE` para que o arquivo rode sem dependências.

```sql
-- num banco vazio: executar schema.sql, depois prevencao.sql e, na mesma sessão:
INSERT INTO arquivo_recebido (nome, sha256) VALUES ('pagamentos.json', repeat('a', 64));
EXECUTE gravar_precatorio('00123451020198260053', 'Município de São Paulo', 45000, '2025-04-02');
EXECUTE gravar_pagamento('00123451020198260053', 'ev-001', 'pagamento', 15500, '2025-03-10', '2025-03-11T09:00:00-03:00', 1);
EXECUTE gravar_pagamento('00123451020198260053', 'ev-001', 'pagamento', 15000, '2025-03-10', '2025-03-10T10:00:00-03:00', 1);
SELECT * FROM v_total_pago;      -- 15500.00: a versão antiga do ev-001 não substitui a nova
SELECT * FROM v_prazo_pagamento; -- 2026-12-31: apresentado em 02/04, prazo no ano seguinte
```

## Uso de IA

Usei Claude Code no desenvolvimento e Gemini para revisões cruzadas.

**Onde a IA me ajudou:**
- Primeira versão do código, dos testes e da documentação, que depois revisei e refinei.
- Apoio na análise dos dados e na revisão final.

**O que foi feito por mim:**
- A investigação dos dados e a priorização dos problemas.
- As decisões de regra, como tratar `evento_id` repetido como atualização e recusar valores ambíguos em vez de adivinhar.
- A arquitetura da solução, com o `limpeza.py` servindo como camada de validação antes da ingestão, a gravação por `evento_id` e o total calculado no banco.

**Como conferi:**
- Testes automatizados para as regras de maior risco, rodados a cada mudança, e conferência manual dos totais contra os arquivos originais (ver Testes).
- O SQL foi executado num PostgreSQL local.
- Revisão manual de todo o código e da documentação gerados antes de cada commit.