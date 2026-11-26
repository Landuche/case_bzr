-- o hash identifica o mesmo arquivo chegando de novo
CREATE TABLE arquivo_recebido (
    id          BIGSERIAL PRIMARY KEY,
    nome        TEXT        NOT NULL,
    sha256      CHAR(64)    NOT NULL UNIQUE,
    recebido_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- versão do evento e arquivo de origem
ALTER TABLE pagamento ADD COLUMN atualizado_em TIMESTAMPTZ;
ALTER TABLE pagamento ADD COLUMN arquivo_id    BIGINT REFERENCES arquivo_recebido (id);

-- linhas antigas ficam sem atualizado_em, o NOT VALID exige o campo só das novas
ALTER TABLE pagamento ADD CONSTRAINT ck_atualizado_em CHECK (atualizado_em IS NOT NULL) NOT VALID;

-- Antes do UNIQUE, os duplicados que já estão no banco precisam ser resolvidos
-- a base limpa do limpeza.py serve de referência. Essa consulta precisa voltar vazia
SELECT evento_id, count(*) FROM pagamento GROUP BY evento_id HAVING count(*) > 1;

ALTER TABLE pagamento ADD CONSTRAINT uq_pagamento_evento UNIQUE (evento_id);

-- processo só com os 20 dígitos e data de apresentação
-- Antes do CHECK, os processos fora do formato precisam ser normalizados. Essa consulta precisa voltar vazia
SELECT numero_processo FROM precatorio WHERE numero_processo !~ '^[0-9]{20}$';

ALTER TABLE precatorio ADD CONSTRAINT ck_numero_processo CHECK (numero_processo ~ '^[0-9]{20}$');
ALTER TABLE precatorio ADD COLUMN apresentado_em DATE;

-- Só os eventos que ficaram fora da base (descartados e em dúvida deixados de fora)
CREATE TABLE evento_rejeitado (
    id           BIGSERIAL PRIMARY KEY,
    arquivo_id   BIGINT      REFERENCES arquivo_recebido (id),
    evento_id    TEXT,
    categoria    TEXT        NOT NULL CHECK (categoria IN ('descartado', 'em_duvida')),
    motivo       TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    criado_em    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolvido_em TIMESTAMPTZ
);

-- gravação do precatório a partir do precatorios.json
INSERT INTO precatorio (numero_processo, ente_devedor, valor_face, apresentado_em)
VALUES ($1, $2, $3, $4)
ON CONFLICT (numero_processo) DO UPDATE
    SET ente_devedor   = EXCLUDED.ente_devedor,
        valor_face     = EXCLUDED.valor_face,
        apresentado_em = EXCLUDED.apresentado_em;

-- reimportar não duplica, e só uma versão mais recente substitui a atual
-- linhas antigas, sem atualizado_em, aceitam a primeira versão que chegar
INSERT INTO pagamento (precatorio_id, evento_id, tipo, valor, pago_em, atualizado_em, arquivo_id)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (evento_id) DO UPDATE
    SET precatorio_id = EXCLUDED.precatorio_id,
        tipo          = EXCLUDED.tipo,
        valor         = EXCLUDED.valor,
        pago_em       = EXCLUDED.pago_em,
        atualizado_em = EXCLUDED.atualizado_em,
        arquivo_id    = EXCLUDED.arquivo_id
    WHERE pagamento.atualizado_em IS NULL
       OR EXCLUDED.atualizado_em > pagamento.atualizado_em;

-- Totais calculados a partir dos eventos, para todos os precatórios (inclusive sem pagamento)
CREATE VIEW v_total_pago AS
SELECT p.id AS precatorio_id,
       p.numero_processo,
       p.valor_face,
       COALESCE(SUM(g.valor) FILTER (WHERE g.tipo = 'pagamento'), 0)                AS total_pagamentos,
       COALESCE(SUM(g.valor) FILTER (WHERE g.tipo = 'estorno'), 0)                  AS total_estornos,
       COALESCE(SUM(CASE WHEN g.tipo = 'estorno' THEN -g.valor ELSE g.valor END), 0) AS total_liquido,
       p.valor_face - COALESCE(SUM(CASE WHEN g.tipo = 'estorno' THEN -g.valor ELSE g.valor END), 0) AS saldo
FROM precatorio p
LEFT JOIN pagamento g ON g.precatorio_id = p.id
GROUP BY p.id, p.numero_processo, p.valor_face;

-- Prazo calculado a partir da data de apresentação
CREATE VIEW v_prazo_pagamento AS
SELECT id AS precatorio_id,
       numero_processo,
       apresentado_em,
       make_date(EXTRACT(YEAR FROM apresentado_em)::int
                 + CASE WHEN apresentado_em <= make_date(EXTRACT(YEAR FROM apresentado_em)::int, 4, 2)
                        THEN 1 ELSE 2 END, 12, 31) AS prazo_pagamento
FROM precatorio
WHERE apresentado_em IS NOT NULL;