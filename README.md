# Sistema Papelaria — ArtColore

PDV, estoque, materiais/serviços, clientes, fornecedores, preços e relatórios em **Flask** com dados em **JSON**.
> ⚠️ **Modo de teste** — sem preocupação com segurança nesta fase.

## Executar (Windows, sem ambiente virtual)
```powershell
python -m pip install --user -r requirements.txt   # primeira vez / após atualizar
python run.py --reset                               # recria dados de demonstração
python run.py                                       # http://127.0.0.1:5000
```
Se `python` não for reconhecido, use `py`.

## Usuários (`usuarios_padrao.json`)
| Login | Senha | Grupo |
|-------|-------|-------|
| admin | admin | Administrador |
| gerente | gerente | Gerente |
| estoque | estoque | Estoquista |
| caixa | caixa | Vendedor / Caixa |

## Status
| Fase | Situação |
|------|----------|
| F0 — Infra, login, usuários, grupos, auditoria | ✅ |
| F1 — Cadastros (materiais/serviços, tipos, fornecedores, clientes, localizações, imagens) | ✅ |
| F2 — Estoque (compra, sucata, doação, transferência, ajuste, inventário, saldo por local, lotes/validade FEFO, alertas) | ✅ |
| F3 — PDV (caixa único, venda, pagamento manual, descontos, comprovante PDF, cancelamento, sangria/suprimento, fechamento) | ✅ |
| F4 — Devolução/Troca (7 dias, crédito do cliente, autorização de supervisor) | ✅ |
| F5 — Preços (promoções, reajuste em massa %/R$, histórico) | ✅ |
| F6 — Códigos (leitor USB, câmera, EAN-13 interno, QR Code) | ✅ (etiquetas impressas: a definir) |
| F7 — Painel, relatórios (tela/Excel/CSV/PDF), configurações, backups | ✅ |
| F8 — PostgreSQL · F9 — NFC-e · F10 — Impressão | ⏳ |

## Comandos
| Comando | Ação |
|---------|------|
| `python run.py` | Sincroniza usuários de teste e inicia |
| `python run.py --reset` | Apaga dados (`data/`) e recria a demonstração |
| `python run.py --backup` | Gera `data/backups/backup_*.zip` |

## Dados
- `data/*.json` — cadastros, vendas, saldos; `data/movimentacoes/AAAA-MM.json`
- `data/comprovantes/AAAA/MM/*.pdf` — comprovantes (venda, devolução, cancelamento, caixa)
- `data/uploads/` — imagens · `data/backups/` — backups
- A câmera do celular para leitura de código exige **HTTPS** (ou `localhost`).
