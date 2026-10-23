# 📦 Sistema de Controle de Papelaria — Documento de Requisitos

> **Idioma do sistema:** Português (PT-BR) — toda interface, mensagens, relatórios e formatos (data `dd/mm/aaaa`, moeda `R$ 1.234,56`, fuso `America/Sao_Paulo`).
> **Versão do documento:** 0.6

### Histórico de versões
| Versão | Alterações |
|--------|-----------|
| 0.1 | Rascunho inicial: estoque, cadastro de materiais, preços, código de barras/QR Code |
| 0.2 | Inclusão de PDV, NFC-e, localização física, cadastro de clientes, grupos de usuários; arquitetura Flask + Apache + AWS EC2; tipos de movimentação definidos |
| 0.3 | NFC-e adiada para fase posterior; pagamento em serviço externo lançado manualmente; persistência inicial em **arquivos JSON** com migração futura para banco na mesma EC2; inclusão de **Devolução** e **Troca** |
| 0.4 | Devolução gera **somente crédito** ao cliente; prazo de **7 dias** para devolução e troca; **1 caixa (PDV)**; comprovantes salvos como **arquivos PDF** |
| 0.5 | Crédito do cliente **não expira**; **Personalizados** passam a ser **serviços** (Montagem, Impressão, Formatação, Encadernação), sem controle de estoque, e **podem ser devolvidos** (gera crédito) |
| 0.6 | Serviços: **sem consumo de estoque** na fase inicial, **sem ordem de serviço**, **preço definido no momento da venda**; na fase fiscal, serviços são emitidos **como produtos na NFC-e** |

---

## 1. Visão Geral

Sistema web para gestão de uma papelaria/loja multiprodutos com **ponto de venda (PDV)**, cadastro de materiais, clientes e fornecedores, controle de estoque com **localização física**, venda de **serviços personalizados**, devoluções/trocas com **crédito do cliente**, alertas de estoque e validade, e identificação por **código de barras** e **QR Code**. Emissão de **NFC-e** prevista para fase posterior.

### 1.1 Objetivos
- Centralizar o cadastro de materiais, fornecedores e clientes.
- Realizar vendas no PDV com registro manual de pagamento e comprovante em PDF.
- Controlar entradas, saídas e saldo de estoque em tempo real, por localização.
- Tratar devoluções e trocas, gerando crédito para o cliente.
- Calcular preços de compra/venda de forma dinâmica por fornecedor.
- Gerenciar promoções e reajustes (percentual e valor absoluto).
- Emitir alertas de estoque mínimo, estoque zerado e validade.
- Controlar acesso por usuários e grupos de permissão.

### 1.2 Módulos
| # | Módulo | Descrição |
|---|--------|-----------|
| 1 | Ponto de Venda (PDV) | Venda por leitura de código, registro manual de pagamento, comprovante PDF |
| 2 | Devolução e Troca | Devoluções/trocas em até 7 dias, crédito do cliente |
| 3 | Controle de Estoque | Saldo, movimentações, inventário, alertas |
| 4 | Localização Física | Endereçamento de prateleiras e saldo por local |
| 5 | Cadastro de Materiais e Serviços | Produtos (com estoque) e serviços personalizados (sem estoque) |
| 6 | Fornecedores | Cadastro e tabela de preços por fornecedor |
| 7 | Clientes | Cadastro básico (CPF, nome, telefone) e saldo de crédito |
| 8 | Código de Barras / QR Code | Leitura, geração e etiquetas |
| 9 | Preços e Promoções | Preço dinâmico, promoções e reajustes |
| 10 | Usuários e Grupos | Login, perfis, permissões e auditoria |
| 11 | Painel e Relatórios | Indicadores, alertas e exportações |
| 12 | Fiscal (NFC-e) — **fase posterior** | Emissão, cancelamento, contingência |

---

## 2. Arquitetura e Infraestrutura

### 2.1 Stack
| Camada | Tecnologia |
|--------|-----------|
| Servidor | **AWS EC2** (Linux — Ubuntu Server LTS ou Amazon Linux) |
| Servidor web | **Apache HTTP Server** + **mod_wsgi** |
| Backend | **Python 3 + Flask** |
| Persistência (fase inicial) | **Arquivos JSON** via camada de repositório (seção 2.4) |
| Banco de dados (fase futura) | **PostgreSQL na mesma instância EC2** + Flask-SQLAlchemy + Flask-Migrate |
| Autenticação | Flask-Login + hash de senha (werkzeug/bcrypt) |
| Formulários / CSRF | Flask-WTF |
| Frontend | Jinja2 + HTML/CSS/JS (Bootstrap), responsivo |
| Leitura por câmera | Biblioteca JS (ex.: html5-qrcode / ZXing) — **exige HTTPS** |
| Geração de códigos | `python-barcode` / `qrcode` |
| Comprovantes | **PDF** gerado no servidor com **ReportLab** (seção 3.7) |
| Relatórios | `openpyxl` (Excel), CSV, PDF (ReportLab) |
| Imagens | Disco da EC2 (`/var/www/papelaria/data/uploads`) |
| Concorrência JSON | `filelock` |

### 2.2 Implantação
- Apache com `VirtualHost` + `WSGIDaemonProcess` apontando para `app.wsgi`.
- Ambiente virtual Python (`venv`) dedicado.
- **HTTPS obrigatório** (Let's Encrypt/Certbot) — necessário para câmera e segurança.
- Security Group liberando apenas 80/443 (22 restrito ao IP administrativo).
- Variáveis sensíveis (chave secreta etc.) em `.env` fora do diretório público.
- Backup diário automático da pasta `data/` (JSON + imagens + comprovantes PDF), compactado com data no nome (ex.: `backup_2026-10-21.zip`), mantendo os últimos N dias. Snapshot periódico do volume EBS.
- Logs: Apache (`access`/`error`) + log da aplicação com rotação.

### 2.3 Estrutura sugerida do projeto
```
papelaria/
├── app/
│   ├── __init__.py          # create_app()
│   ├── models/              # dataclasses (JSON) → SQLAlchemy no futuro
│   ├── repositories/        # camada de acesso a dados (JSON / BD)
│   ├── services/            # regras de negócio (venda, estoque, devolução, pdf)
│   ├── blueprints/
│   │   ├── auth/            # login, usuários, grupos
│   │   ├── pdv/             # caixa, venda, devolução/troca
│   │   ├── estoque/
│   │   ├── materiais/
│   │   ├── fornecedores/
│   │   ├── clientes/
│   │   ├── localizacoes/
│   │   └── relatorios/
│   ├── templates/
│   └── static/
├── data/                    # JSON + arquivos (fora do diretório público)
│   ├── *.json
│   ├── movimentacoes/
│   ├── uploads/
│   ├── comprovantes/        # PDFs: AAAA/MM/*.pdf
│   └── backups/
├── config.py
├── app.wsgi
├── requirements.txt
└── .env
```

### 2.4 Persistência em JSON (fase inicial)

**Decisão:** iniciar com arquivos JSON e migrar para PostgreSQL na mesma EC2 posteriormente.

**Arquivos (um por entidade):**
```
data/
├── usuarios.json          ├── grupos.json            ├── permissoes.json
├── materiais.json         ├── tipos_material.json    ├── fornecedores.json
├── precos_fornecedor.json ├── promocoes.json         ├── historico_precos.json
├── clientes.json          ├── creditos_cliente.json  ├── localizacoes.json
├── lotes.json             ├── saldos.json            ├── inventarios.json
├── caixas.json            ├── vendas.json            ├── devolucoes.json
├── auditoria.json         ├── configuracao.json      └── sequencias.json
└── movimentacoes/
    └── 2026-10.json       # particionado por mês
```

**Regras técnicas obrigatórias:**
| Regra | Motivo |
|-------|--------|
| Acesso **somente pela camada `repositories/`** (`get`, `list`, `add`, `update`) | Trocar JSON → PostgreSQL sem alterar rotas |
| **Lock de arquivo** (`filelock`) em toda escrita | Apache/mod_wsgi usa threads |
| **Escrita atômica** (grava `.tmp` + `os.replace`) | Evita arquivo corrompido |
| Operações em vários arquivos (venda = venda + movimentações + saldos + crédito) sob **um lock global de transação** | Consistência sem banco |
| IDs sequenciais em `sequencias.json` (`MAT-000001`, `VEN-000001`, `DEV-000001`) | IDs legíveis e únicos |
| `mod_wsgi` com **1 processo** (`processes=1 threads=N`) | Reduz concorrência |
| `data/` fora do `DocumentRoot`, permissão apenas do usuário do Apache | Segurança |
| Datas ISO 8601 (`2026-10-21T10:00:00-03:00`); valores monetários em **centavos (inteiro)** | Evita erros de arredondamento |
| Campo `versao_schema` em cada arquivo | Facilita migrações |
| Comandos `flask seed`, `flask backup`, `flask restore` | Testes e recuperação |

**Critérios para migrar para o banco:** inclusão de um segundo caixa/PDV (hoje: **1 caixa**), lentidão perceptível (> ~5.000 materiais ou arquivos de movimentação grandes) ou início da fase fiscal. Migração via `flask migrar-json`.

---

## 3. Ponto de Venda (PDV)

### 3.1 Fluxo de venda
1. Abrir caixa (usuário + valor inicial de troco).
2. Identificar cliente (opcional): busca por **CPF**, nome ou telefone; cadastro rápido; ou "Consumidor não identificado". Ao identificar, exibir **saldo de crédito**.
3. Adicionar itens (produtos ou **serviços**) por **leitor de código de barras/QR** (Enter), busca por nome/código ou câmera; atalhos rápidos para os serviços.
4. Informar quantidade (inteiro para item/caixa/serviço; decimal para metro). Para **serviços**, o operador **informa o preço no momento da venda** (obrigatório, > R$ 0,00) e pode incluir **observação** (ex.: "encadernação espiral 50 folhas, capa azul").
5. Preço de produtos aplicado automaticamente (promocional se vigente); serviços usam o preço informado no passo 4.
6. Desconto no item ou no total (% ou R$) — limitado por permissão do grupo.
7. Lançar pagamentos (seção 3.2).
8. Finalizar: baixa no estoque (movimentação **Venda**) **somente para produtos** — serviços não movimentam estoque — e geração do **comprovante não fiscal em PDF** (seção 3.7).

### 3.2 Pagamento (serviço externo, lançamento manual)
O pagamento é **processado fora do sistema** (maquininha/app/Pix do banco). O operador **registra manualmente** o que foi recebido — **sem integração (TEF/API)**.

| Campo lançado | Obrigatório | Observação |
|---------------|:-----------:|-----------|
| Forma | ✅ | Dinheiro, Pix, Débito, Crédito, **Crédito do cliente (vale)**, Outros |
| Valor | ✅ | Várias formas na mesma venda |
| Parcelas | Condicional | Somente cartão de crédito |
| Código de autorização / NSU / ID da transação | ⬜ | Conferência com o extrato do serviço externo |
| Troco | Auto | Somente dinheiro |

- Venda só finaliza quando **soma dos pagamentos ≥ total**.
- **Crédito do cliente** exige cliente identificado (CPF), limitado ao saldo; abatido ao finalizar; não gera troco.

### 3.3 Caixa
- **Um único caixa (PDV)** nesta fase — apenas uma sessão de caixa aberta por vez.
- Abertura e fechamento por operador (troca de operador exige fechar e reabrir).
- Sangria e suprimento (com motivo).
- Fechamento: total por forma de pagamento, valor contado × esperado, diferenças, cancelamentos, devoluções/trocas, créditos gerados/utilizados — salvo em **PDF**.

### 3.4 Cancelamento
- Cancelamento de item antes de finalizar.
- Cancelamento de venda finalizada (somente no mesmo dia e com permissão): estorno do estoque e comprovante de cancelamento em PDF.

### 3.5 Devolução e Troca
| Operação | Descrição | Efeito no estoque | Efeito financeiro |
|----------|-----------|-------------------|-------------------|
| **Devolução** | Cliente devolve item(s) de uma venda | ➕ Entrada (movimentação **Devolução**) — produtos; **serviços: sem efeito no estoque** | **Crédito para o cliente** no valor pago — **sem reembolso em dinheiro/estorno** |
| **Troca** | Cliente devolve item(s) e leva outro(s) | ➕ Entrada dos devolvidos + ➖ saída dos novos (movimentação **Troca**) | Diferença a pagar pelo cliente (lançamento manual, 3.2) **ou** saldo a favor gerado como **crédito** |

**Fluxo:**
1. Localizar a venda (nº do comprovante, CPF ou leitura do QR Code do comprovante PDF).
2. Validar prazo de **7 dias corridos** a partir da data da venda.
3. Selecionar itens e quantidades (≤ vendido − já devolvido).
4. Informar **motivo** (defeito, arrependimento, tamanho/modelo, outro) e **condição**:
   - **Revenda** → volta ao saldo disponível na localização escolhida.
   - **Avariado** → entra e gera automaticamente **Sucata**.
5. Troca: adicionar itens novos; sistema calcula a diferença.
6. Devolução: gerar **crédito do cliente**. Troca: lançar pagamento da diferença ou gerar crédito com o saldo a favor.
7. Gerar comprovante de devolução/troca em **PDF**, com saldo de crédito atualizado.

**Regras:**
- **Prazo: 7 dias corridos** para devolução e troca (configurável em `configuracao.json`); fora do prazo exige permissão de gerente.
- **Devolução nunca gera reembolso em dinheiro** — sempre crédito vinculado ao CPF.
- Venda sem cliente identificado: devolução apenas com o comprovante e **cadastro do cliente (CPF) obrigatório no ato**.
- Valor creditado = preço efetivamente pago (promoção e desconto rateado).
- **Serviços personalizados podem ser devolvidos** dentro dos mesmos 7 dias: geram crédito ao cliente, sem movimentação de estoque (não há condição revenda/avariado nem localização).
- Crédito **não expira**; utilizável total ou parcialmente no PDV; não pode ser convertido em dinheiro nem gerar troco.
- Requer permissão `pdv.devolucao` / `pdv.troca`.

### 3.6 Regras gerais do PDV
- Não vender item **vencido**, **inativo** ou sem saldo (validação de saldo não se aplica a serviços).
- Ao vender por item um material armazenado em caixa, a conversão caixa → itens é automática.

### 3.7 Comprovantes em PDF
Nesta fase, todos os comprovantes são **gerados e salvos como PDF** no servidor (impressão física a definir).

| Documento | Nome do arquivo | Gerado em |
|-----------|-----------------|-----------|
| Venda | `VEN-000001_2026-10-21_143512.pdf` | Finalização da venda |
| Devolução/Troca | `DEV-000001_2026-10-21_150201.pdf` | Finalização da devolução/troca |
| Cancelamento | `CAN-000001_2026-10-21_151000.pdf` | Cancelamento |
| Fechamento de caixa | `CX-000001_2026-10-21_220000.pdf` | Fechamento |

- Local: `data/comprovantes/AAAA/MM/` (fora do diretório público; download via rota autenticada).
- Layout **80 mm** de largura (pronto para futura impressora térmica), legível em tela/A4.
- Conteúdo da venda: dados da loja, nº da venda, data/hora, operador, cliente (CPF mascarado `***.456.789-**`), itens (qtd, unidade, preço, promoção, desconto), total, pagamentos (forma, valor, NSU), troco, crédito utilizado e saldo, **"Documento sem valor fiscal"**, prazo de troca (data limite), **QR Code com o nº da venda**.
- Após finalizar: abrir o PDF em nova aba (imprimir/salvar/compartilhar).
- **Reemissão:** baixar o PDF original (nunca regerado/alterado).
- Caminho salvo no registro (`venda.comprovante_pdf`, `devolucao.comprovante_pdf`).

---

## 4. Cadastro de Materiais

### 4.1 Campos
| Campo | Tipo | Obrigatório | Observação |
|-------|------|:-----------:|------------|
| Natureza | Lista | ✅ | **Produto** (controla estoque) ou **Serviço** (tipo Personalizados) |
| Código interno | Texto (auto) | ✅ | Ex.: `MAT-000001` (produto) / `SRV-000001` (serviço) |
| Código de barras | Texto | ⬜ | EAN-13 / EAN-8 / Code128; lido ou gerado |
| QR Code | Texto (auto) | ✅ | Gerado a partir do código interno |
| Nome | Texto (até 120) | ✅ | |
| Descrição | Texto longo | ⬜ | |
| Tipo | Lista | ✅ | Seção 4.6 |
| Fornecedor(es) | Relação N:N | ✅ | Ao menos 1; um **principal** (opcional para serviços) |
| Preço de compra | Moeda | ✅ | Por fornecedor (seção 5) |
| Preço de venda | Moeda | ✅ | Calculado ou manual |
| Valor promocional | Moeda | ⬜ | Com vigência |
| Imagens | Arquivos | ⬜ | Múltiplas; uma **principal** |
| Unidade de medida | Lista | ✅ | Seção 4.3 |
| Quantidade em estoque | Decimal (calculado) | ✅ | Soma dos saldos por localização |
| Localização padrão | Relação | ⬜ | Seção 8 |
| Estoque mínimo (alerta) | Decimal | ✅ | Seção 4.4 |
| Controla validade? | Sim/Não | ✅ | |
| Dados fiscais | — | ⬜ | Seção 13.3 — opcional até ativar NFC-e (produtos **e serviços**) |
| Ativo | Sim/Não | ✅ | |
| Criado/alterado em/por | Auto | ✅ | Auditoria |

### 4.2 Imagens
- Upload múltiplo (JPG, PNG, WEBP) e captura pela câmera.
- Compressão automática (máx. 1024 px, ~200 KB).
- Imagem principal exibida em listas, PDV e etiquetas.

### 4.3 Quantidade / Unidade de Medida
| Unidade | Subtipo | Exemplo | Regra |
|---------|---------|---------|-------|
| **Metro (m)** | — | Fitas, tecidos, elásticos | Aceita decimais (ex.: 2,5 m) |
| **Unitário** | **Caixa fechada** | Caixa com 50 canetas | Possui **fator de conversão** |
| **Unitário** | **Item** | 1 caneta | Quantidade inteira |
| **Serviço** | Unidade | 1 encadernação, 1 impressão | Quantidade inteira; sem saldo; preço informado na venda |

- Saldo armazenado sempre na **menor unidade** e exibido nos dois formatos (ex.: 137 itens, caixa = 50 → **2 caixas + 37 itens**).
- Preço por caixa e por item (item pode ser derivado: `preço caixa ÷ fator`).

### 4.4 Estoque Mínimo e Alertas
| Alerta | Condição | Cor |
|--------|----------|-----|
| ⚠️ Estoque baixo | `0 < quantidade ≤ mínimo` | Amarelo |
| ⛔ Estoque zerado | `quantidade = 0` | Vermelho |

- Exibidos no painel, listagem, PDV e como notificação ao entrar.
- **Sugestão de compra** agrupada por fornecedor principal (`mínimo × 2 − saldo`, configurável).

### 4.5 Data de Validade
- Controle opcional por material, registrado **por lote** na Compra.
- Saída segue **FEFO** (vence primeiro, sai primeiro).

| Alerta | Condição (configurável) |
|--------|-------------------------|
| 🟡 Próximo do vencimento | ≤ 30 dias |
| 🔴 Vencido | Data < hoje |

- Itens vencidos bloqueados para Venda/Doação/Troca — saída somente como **Sucata**.

### 4.6 Tipos (Categorias)
| Tipo | Natureza |
|------|----------|
| Papelaria | Produto |
| Presentes | Produto |
| Eletrônicos | Produto |
| Brinquedos | Produto |
| Aviamentos | Produto |
| Cosméticos | Produto |
| **Personalizados** | **Serviço** |

> Mantidos em arquivo próprio para inclusão futura.

### 4.7 Serviços Personalizados
Itens do tipo **Personalizados** são **serviços** prestados na loja:

| Serviço | Exemplo |
|---------|---------|
| **Montagem** | Montagem de kits, caixas, presentes |
| **Impressão** | Impressão de documentos, fotos, artes |
| **Formatação** | Formatação de documentos/trabalhos |
| **Encadernação** | Espiral, capa dura, térmica |

> Lista de serviços cadastrável — novos serviços podem ser incluídos.

**Campos aplicáveis ao serviço:**
| Campo | Aplica |
|-------|:------:|
| Nome, descrição, código interno, código de barras/QR (para lançamento rápido no PDV) | ✅ |
| Preço de venda | ⬜ **apenas referência** (sugerido no PDV) — valor real informado na venda |
| Valor promocional, reajustes em massa | ❌ |
| Imagens | ✅ |
| Unidade de cobrança | ✅ (padrão: unidade) |
| Fornecedor / preço de compra | ⬜ opcional (ex.: serviço terceirizado) |
| Dados fiscais (NCM, CFOP etc.) | ⬜ até a fase fiscal — serviço será emitido **como produto** na NFC-e (seção 13) |
| Quantidade em estoque, estoque mínimo, alertas, localização, lote, validade | ❌ |

**Regras:**
- **Preço definido no momento da venda** para todos os serviços (inclusive impressão) — sem tabela de preços fixa.
- Serviços **não geram movimentação de estoque** e **não consomem materiais** nesta fase (consumo automático de insumos poderá ser avaliado no futuro).
- **Sem ordem de serviço**: o serviço é vendido e finalizado no PDV como qualquer item (sem prazo/status de produção).
- Não aparecem em inventário, sugestão de compra, alertas de estoque ou validade.
- Na venda, campo de **observação** por item para detalhes do serviço (aparece no comprovante PDF).
- Desconto por item não se aplica (o preço já é informado); desconto no total da venda continua permitido.
- **Podem ser devolvidos/trocados** em até 7 dias, gerando crédito ao cliente.

---

## 5. Preços (Compra / Venda)

### 5.1 Preço Dinâmico por Fornecedor
| Fornecedor | Preço de compra | Unidade | Atualização | Principal |
|-----------|----------------:|---------|-------------|:---------:|
| Fornecedor A | R$ 25,00 | Caixa (50) | 10/10/2026 | ✅ |
| Fornecedor B | R$ 0,55 | Item | 05/09/2026 | ⬜ |

- Regra do preço de venda:
  - **Markup %:** `venda = compra × (1 + markup%)`
  - **Valor fixo:** `venda = compra + R$ X`
  - **Manual**
- Base de cálculo: fornecedor principal, **menor preço** ou **último preço de compra**.
- Movimentação de **Compra** atualiza o preço do fornecedor e recalcula a venda (exceto regra manual).
- Histórico de preços com data e usuário.

### 5.2 Valor Promocional
- Vigência com **data início** e **data fim**.
- Definido por valor final, desconto **%** ou desconto **R$**.
- PDV, comprovante e etiquetas exibem o promocional (original riscado).
- Alerta se promocional < preço de compra.

### 5.3 Variação de Preço (Reajuste)
- Individual ou **em massa** (por tipo, fornecedor ou seleção), em **%** ou **R$**, sobre compra, venda ou ambos.
- Exibir variação em relação ao preço anterior (**% e R$**) e margem atual (**% e R$**).
- Pré-visualização antes de confirmar.

---

## 6. Fornecedores
| Campo | Obrigatório |
|-------|:-----------:|
| Razão social / Nome fantasia | ✅ |
| CNPJ / CPF (com validação) | ⬜ |
| Contato (nome, telefone, WhatsApp, e-mail) | ⬜ |
| Endereço | ⬜ |
| Prazo médio de entrega (dias) | ⬜ |
| Observações | ⬜ |
| Ativo | ✅ |

---

## 7. Clientes (Cadastro Básico)
| Campo | Tipo | Obrigatório | Observação |
|-------|------|:-----------:|-----------|
| CPF | Texto (máscara `000.000.000-00`) | ✅ | Validação de dígitos; **único** |
| Nome | Texto | ✅ | |
| Telefone | Texto (máscara `(00) 00000-0000`) | ✅ | |
| Saldo de crédito | Moeda (calculado) | Auto | Soma do extrato de créditos |
| Criado em / por | Auto | ✅ | |

- Busca por CPF, nome ou telefone (também no PDV); cadastro rápido no PDV.
- Histórico de compras, devoluções e trocas.
- **Extrato de crédito**: entradas (devoluções/trocas) e usos (vendas), com saldo após cada lançamento. **Crédito não expira.**
- **LGPD**: acesso restrito por permissão; CPF mascarado em listagens e comprovantes.

---

## 8. Localização Física (Endereçamento)

### 8.1 Estrutura
```
Área (Loja / Depósito) → Corredor → Prateleira → Nível/Posição
Ex.: LOJA-C02-P05-N3
```
| Campo | Observação |
|-------|-----------|
| Código | Único, ex.: `LOJA-C02-P05-N3` |
| Descrição | |
| Área | Loja, Depósito, … |
| QR Code | Gerado para etiqueta da prateleira |
| Ativo | |

### 8.2 Regras
- Saldo por **material × localização (× lote, se controla validade)**.
- Material pode estar em várias localizações; uma **padrão**.
- Compra e Devolução/Troca informam localização de destino (padrão sugerida).
- Venda baixa da localização da **Loja** (ordem configurável/FEFO).
- **Transferência entre localizações** (não altera saldo total).
- Inventário por localização: ler QR da prateleira → ler itens.
- Consulta "onde está o material?" com saldo por endereço.

---

## 9. Controle de Estoque

### 9.1 Tipos de Movimentação
| Tipo | Efeito | Dados obrigatórios | Origem |
|------|:------:|--------------------|--------|
| **Compra** | ➕ | Fornecedor, preço unitário, quantidade, unidade, localização; lote/validade (se aplicável); nº NF de entrada (opcional) | Tela de entrada |
| **Venda** | ➖ | Venda vinculada, preço praticado | Automática pelo PDV |
| **Devolução** | ➕ | Venda original, motivo, condição (revenda/avariado), localização — somente produtos | PDV — Devolução |
| **Troca** | ➕/➖ | Venda original, itens devolvidos (entrada) e novos (saída), motivo, diferença | PDV — Troca |
| **Sucata / Descarte** | ➖ | Motivo (avaria, vencido, defeito, outro), observação | Tela de saída / automática (devolução avariada) |
| **Doação** | ➖ | Destinatário (instituição/pessoa), motivo | Tela de saída |
| **Ajuste de Inventário** | ➕/➖ | Quantidade contada, diferença, motivo | Tela de inventário |

**Operações auxiliares** (registradas no histórico):
- Transferência entre localizações.
- Conversão de caixa fechada em itens.
- Estorno por cancelamento de venda.

### 9.2 Regras
- Movimentações aplicam-se **somente a produtos**; serviços não movimentam estoque.
- Toda movimentação registra: data/hora, usuário, material, quantidade, unidade, localização, lote, tipo, valor e observação.
- Movimentações **imutáveis** — correções somente por estorno/ajuste.
- Saldo negativo bloqueado.
- Sucata, Doação e Ajuste exigem permissão específica.
- Custo de Sucata/Doação pelo **custo médio** (relatório de perdas).

### 9.3 Inventário
- Abrir inventário (geral, por tipo ou por localização).
- Contagem por leitura de código/QR ou digitação.
- Relatório de divergências antes de confirmar.
- Confirmação gera movimentações de **Ajuste de Inventário**.

---

## 10. Código de Barras e QR Code
### 10.1 Leitura
- Leitor USB/Bluetooth (modo teclado, dispara no **Enter**).
- Câmera (celular/webcam) — EAN-13, EAN-8, Code128 e QR Code.
- Ação rápida após leitura: consulta, compra, saída, transferência, adicionar ao PDV.
- Leitura do QR do comprovante abre a venda (para devolução/troca).
- Código não encontrado → oferecer cadastro com o código preenchido.

### 10.2 Geração
- Code128 ou EAN-13 com prefixo `2xx` para itens sem código de fábrica.
- QR Code de material, de **localização** e de **venda** (comprovante).

### 10.3 Etiquetas
> 🕓 **Modelo de impressão a definir.** Prever conteúdo: nome, preço (e promocional), código de barras e/ou QR Code, e etiqueta de prateleira.

---

## 11. Usuários e Grupos de Permissão

### 11.1 Usuário
| Campo | Obrigatório |
|-------|:-----------:|
| Nome | ✅ |
| Login (usuário/e-mail) | ✅ |
| Senha (hash) | ✅ |
| Grupo(s) | ✅ |
| Ativo | ✅ |
| Último acesso | Auto |

- Troca de senha obrigatória no primeiro acesso; bloqueio após N tentativas falhas.
- Sessão com expiração por inatividade.

### 11.2 Grupos (padrão — editáveis)
| Grupo | Acesso |
|-------|--------|
| **Administrador** | Tudo, incluindo usuários, grupos e configurações |
| **Gerente** | Cadastros, preços, promoções, reajustes, relatórios, cancelamentos, devolução fora do prazo, inventário |
| **Estoquista** | Compra, transferência, inventário, sucata, doação, localizações |
| **Vendedor / Caixa** | PDV, devolução/troca dentro do prazo de 7 dias, cadastro de clientes, consulta de materiais |
| **Consulta** | Somente leitura |

### 11.3 Permissões
- Granulares por módulo e ação (`visualizar`, `criar`, `editar`, `inativar`, ações especiais).
- Ações especiais: `pdv.desconto_acima_limite`, `pdv.cancelar_venda`, `pdv.devolucao`, `pdv.troca`, `pdv.devolucao_fora_prazo`, `estoque.sucata`, `estoque.doacao`, `estoque.ajuste`, `precos.reajuste_massa`, `clientes.ver_dados_completos`, `clientes.ajustar_credito`.
- Limite de desconto (%) por grupo.
- Autorização de supervisor no PDV (senha de gerente para ações restritas).

### 11.4 Auditoria
- Log de login/logout, alterações de cadastro, preços, movimentações, cancelamentos, créditos e permissões — com usuário, data/hora, IP e valor anterior/novo.

---

## 12. Painel e Relatórios

### 12.1 Painel
- Vendas do dia/mês (valor, quantidade, ticket médio, por forma de pagamento).
- Status do caixa (aberto/fechado, operador).
- Valor total em estoque (custo e venda).
- Itens em **estoque baixo**, **zerado**, **próximo do vencimento** e **vencidos**.
- Devoluções/trocas do período e total de créditos em aberto.
- Promoções ativas; últimas movimentações.

### 12.2 Relatórios
- Vendas por período, operador, forma de pagamento, tipo e material — com separação **produtos × serviços**.
- Fechamento de caixa (também em PDF).
- Conferência de pagamentos lançados × serviço externo (por forma, NSU/ID).
- Devoluções e trocas (por motivo, material, operador).
- Créditos de clientes: gerados, utilizados e saldo em aberto.
- Posição de estoque por tipo, fornecedor e **localização**.
- Movimentações por tipo e período (Compra, Venda, Devolução, Troca, Sucata, Doação, Ajuste).
- Perdas (Sucata + Ajustes negativos) e Doações valorizadas ao custo.
- Itens em alerta e sugestão de compra; validade.
- Histórico de preços e margens; curva ABC.
- Exportação: **Excel (.xlsx)**, **CSV**, **PDF**.

---

## 13. Fiscal — NFC-e (🕓 fase posterior)
> Mantido como referência. **Não faz parte das primeiras entregas.**

### 13.1 Requisitos
- NFC-e modelo 65, certificado digital A1, CSC/ID Token.
- Ambientes homologação/produção; autorização, cancelamento, inutilização, contingência, consulta.
- DANFE NFC-e 80 mm; guarda dos XMLs.

### 13.2 Abordagem (a decidir)
| Opção | Prós | Contras |
|-------|------|---------|
| API de terceiros (Focus NFe, NFE.io, PlugNotas) | Rápido, legislação atualizada | Custo mensal/por nota |
| Integração direta (PyNFe) | Sem custo por nota | Manutenção própria |

### 13.3 Serviços na NFC-e
- **Serviços personalizados serão emitidos como produtos** na NFC-e (mesmos campos fiscais), não em NFS-e.
- Cada serviço deverá ter NCM/CFOP/CST definidos pelo contador.

### 13.4 Dados fiscais do material e serviço (opcionais até a ativação)
NCM · CEST · CFOP · Origem · CST/CSOSN · PIS/COFINS · Unidade comercial · GTIN/"SEM GTIN"

> ⚠️ Validar regras tributárias com o contador antes da produção.

---

## 14. Requisitos Não Funcionais
| Requisito | Descrição |
|-----------|-----------|
| Idioma | 100% PT-BR |
| Plataforma | Web responsiva (desktop no PDV, celular/tablet no estoque) |
| Hospedagem | AWS EC2 + Apache + mod_wsgi + Flask |
| Persistência | JSON com lock e escrita atômica → PostgreSQL na mesma EC2 |
| PDV | **1 caixa** |
| Pagamento | Externo, lançado manualmente (sem TEF) |
| Comprovantes | PDF 80 mm salvos em `data/comprovantes/` |
| Segurança | HTTPS, CSRF, senhas com hash, permissões por grupo, segredos fora do código |
| LGPD | Dados de clientes protegidos, CPF mascarado, acesso registrado |
| Desempenho | Busca por código < 1 s; finalização de venda + PDF < 3 s |
| Backup | Diário da pasta `data/` (JSON + imagens + PDFs) |
| Dados de teste | `flask seed` |

---

## 15. Modelo de Dados (entidades)
> Fase inicial: cada entidade é um arquivo JSON (lista de objetos). Os mesmos campos serão tabelas no PostgreSQL.

| Entidade | Principais campos |
|----------|------------------|
| `usuario` | id, nome, login, senha_hash, grupos[], ativo, ultimo_acesso |
| `grupo` | id, nome, limite_desconto, permissoes[] |
| `permissao` | id, codigo, descricao |
| `tipo_material` | id, nome |
| `material` | id, natureza (produto/servico), preco_referencia (serviço), codigo_interno, codigo_barras, nome, descricao, tipo_id, unidade, fator_caixa, estoque_minimo, controla_validade, regra_preco, valor_regra, base_preco, preco_venda, localizacao_padrao_id, fiscal{}, imagens[], ativo |
| `fornecedor` | id, razao_social, fantasia, documento, contato, telefone, email, endereco, prazo_entrega, ativo |
| `preco_fornecedor` | material_id, fornecedor_id, preco_compra, unidade_compra, principal, atualizado_em |
| `promocao` | id, material_id, tipo (valor/percentual/absoluto), valor, inicio, fim |
| `historico_preco` | material_id, campo, anterior, novo, var_pct, var_abs, usuario_id, data_hora |
| `cliente` | id, cpf (único), nome, telefone, criado_em |
| `credito_cliente` | id, cliente_id, tipo (entrada/uso/ajuste), devolucao_id, venda_id, valor, saldo_apos, usuario_id, data_hora |
| `localizacao` | id, codigo, descricao, area, ativo |
| `lote` | id, material_id, validade |
| `saldo` | material_id, localizacao_id, lote_id, quantidade |
| `movimentacao` | id, tipo (compra/venda/devolucao/troca/sucata/doacao/ajuste), material_id, localizacao_id, lote_id, quantidade, valor_unitario, fornecedor_id, venda_id, devolucao_id, destinatario, motivo, usuario_id, data_hora, observacao |
| `inventario` | id, escopo, status, itens[{material_id, localizacao_id, qtd_sistema, qtd_contada}] |
| `caixa` | id, usuario_id, abertura, fechamento, valor_inicial, valor_contado, valor_esperado, movimentos[{tipo sangria/suprimento, valor, motivo}], comprovante_pdf |
| `venda` | id, caixa_id, cliente_id, usuario_id, itens[{material_id, natureza, quantidade, preco_unitario, preco_informado (serviço), desconto, promocional, observacao}], pagamentos[{forma, valor, parcelas, nsu, troco}], subtotal, desconto, total, credito_utilizado, status, data_hora, comprovante_pdf |
| `devolucao` | id, tipo (devolucao/troca), venda_id, cliente_id, usuario_id, motivo, itens[{material_id, quantidade, condicao, localizacao_id, direcao entrada/saida}], valor_devolvido, valor_novos, diferenca, credito_gerado, pagamentos[], data_hora, comprovante_pdf |
| `auditoria` | id, usuario_id, acao, entidade, entidade_id, antes, depois, ip, data_hora |
| `configuracao` | empresa{}, prazo_devolucao_dias: 7, alertas{}, pdv{} |
| `sequencias` | MAT, SRV, VEN, DEV, CAN, CX, MOV, … |

---

## 16. Regras de Negócio (resumo)
1. **RN01** — Saldo na menor unidade (item/metro), por material × localização × lote.
2. **RN02** — Preço de venda recalculado ao alterar preço de compra, exceto regra manual.
3. **RN03** — Promoção válida somente na vigência.
4. **RN04** — Alerta quando venda/promocional < preço de compra.
5. **RN05** — Alerta amarelo `saldo ≤ mínimo`; vermelho `saldo = 0`.
6. **RN06** — Itens vencidos bloqueados para Venda/Doação/Troca; saída somente como Sucata.
7. **RN07** — Saídas por lote seguem FEFO.
8. **RN08** — Código de barras único por material; CPF único por cliente; código de localização único.
9. **RN09** — Movimentações imutáveis; correção somente por estorno/ajuste.
10. **RN10** — Sucata, Doação, Ajuste, cancelamento e desconto acima do limite exigem permissão.
11. **RN11** — Venda finaliza somente quando pagamentos lançados ≥ total.
12. **RN12** — Saldo negativo não permitido.
13. **RN13** — Quantidade devolvida ≤ vendida − já devolvida; valor creditado = valor efetivamente pago.
14. **RN14** — Prazo de devolução/troca: **7 dias corridos** da venda; fora do prazo exige gerente.
15. **RN15** — Devolução gera **somente crédito** vinculado ao CPF; crédito **não expira**, não vira dinheiro nem gera troco.
16. **RN16** — Apenas **uma sessão de caixa** aberta por vez.
17. **RN17** — Todo comprovante é salvo em **PDF** e nunca regerado após emitido.
18. **RN18** — Gravação em JSON atômica e sob lock; acesso a dados somente via repositório.
19. **RN19** — Itens **Personalizados** são serviços: sem estoque, sem consumo de materiais, sem movimentação, sem alertas, sem ordem de serviço; podem ser devolvidos/trocados em 7 dias gerando crédito.
20. **RN20** — Preço de serviço é **obrigatoriamente informado no PDV** a cada venda.
21. **RN21** — Na fase fiscal, serviços são emitidos **como produtos** na NFC-e.

---

## 17. Roadmap
| Fase | Entregas |
|------|----------|
| **F0 — Infra** | EC2, Apache + mod_wsgi, HTTPS, esqueleto Flask, repositório JSON (lock + escrita atômica), login, usuários e grupos, `flask seed` |
| **F1 — Cadastros** | Materiais, **serviços personalizados**, tipos, fornecedores, clientes, localizações, imagens |
| **F2 — Estoque** | Compra, Sucata, Doação, Ajuste, transferência, saldo por localização, alertas |
| **F3 — PDV** | Caixa único, venda, lançamento manual de pagamento, descontos, **comprovante PDF**, cancelamento |
| **F4 — Devolução/Troca** | Devolução e troca (7 dias), crédito do cliente, uso do crédito no PDV, comprovante PDF |
| **F5 — Preços** | Preço por fornecedor, regras, promoções, reajustes, histórico |
| **F6 — Códigos** | Leitura (leitor/câmera), geração de código de barras e QR Code |
| **F7 — Gestão** | Inventário, validade/FEFO, dashboard, relatórios, exportações, backups |
| **F8 — Banco** | Migração JSON → PostgreSQL na mesma EC2 |
| **F9 — Fiscal** | NFC-e |
| **F10** | Impressão de etiquetas e comprovantes (após definição do modelo) |

> **Prioridade imediata:** F0 → F1 (mínimo) → F2 → F3 → F4.

---

## 18. Decisões Tomadas
- ✅ Sistema **com PDV** — **1 caixa**.
- ✅ Hospedagem **AWS EC2 + Apache**, backend **Flask**.
- ✅ Persistência inicial em **JSON**; **PostgreSQL** futuramente **na mesma EC2**.
- ✅ **Pagamento em serviço externo**, registrado **manualmente**.
- ✅ **Comprovantes salvos em PDF** (80 mm).
- ✅ **Devolução e troca** em até **7 dias**; devolução gera **somente crédito**, que **não expira**.
- ✅ **Personalizados = serviços** (Montagem, Impressão, Formatação, Encadernação): sem estoque, **sem consumo de materiais**, **sem ordem de serviço**, **preço informado na venda**, **podem ser devolvidos**; emitidos **como produtos** na NFC-e.
- ✅ **NFC-e** em **fase posterior**.
- ✅ **Localização física** controlada.
- ✅ **Clientes**: CPF, nome, telefone (+ saldo de crédito).
- ✅ Movimentações: **Compra, Venda, Devolução, Troca, Sucata, Doação, Ajuste de Inventário**.
- ✅ **Usuários com grupos** de permissão.
- 🕓 Impressão de etiquetas e comprovantes: **a definir**.

## 19. Pontos em Aberto
- [ ] *(Futuro)* Consumo automático de materiais pelos serviços.
- [ ] *(Fase fiscal)* API de terceiros ou integração direta; regime tributário e UF; classificação fiscal dos serviços (com o contador).
- [ ] *(A definir)* Modelo de impressão de etiquetas e comprovantes.