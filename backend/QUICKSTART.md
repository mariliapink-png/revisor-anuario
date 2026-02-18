# GUIA RÁPIDO - Auditoria Anuário UnB

## ⚡ Instalação e Uso em 3 passos

### Passo 1: Setup
```bash
cd backend
chmod +x run.sh test.sh
```

### Passo 2: Iniciar servidor
```bash
bash run.sh
```

O servidor estará pronto em: **http://localhost:8000**

### Passo 3: Testar (em outro terminal)
```bash
cd backend
bash test.sh
```

---

## 🚀 Primeira Execução Rápida

```bash
# Terminal 1: Iniciar servidor
cd backend
bash run.sh

# Terminal 2: Executar teste
cd backend
bash test.sh
```

Ao terminar o `test.sh`, um relatório HTML será salvo em `/tmp/report_*.html`

---

## 📝 Usando manualmente com curl

```bash
# 1. Criar review
curl -X POST "http://localhost:8000/reviews" \
  -H "Content-Type: application/json" \
  -d '{
    "start_url": "https://anuariounb2025.netlify.app/",
    "report_year": 2025,
    "base_year": 2024
  }'

# Response: {"id": 1, ...}
# Copie o ID da review para os próximos comandos

# 2. Aguardar 5s pela extração do TOC

# 3. Listar seções
curl "http://localhost:8000/reviews/1/sections"

# 4. Rodar checagens em todas as páginas
curl -X POST "http://localhost:8000/reviews/1/run-all?max_pages=50"

# 5. Aguardar 10s

# 6. Exportar relatório
curl "http://localhost:8000/reviews/1/export?format=html"

# Response: {"filename": "report_review_1_XXXXXX.html", ...}

# 7. Baixar
curl "http://localhost:8000/downloads/report_review_1_XXXXXX.html" \
  -o relatorio.html

# 8. Abrir no navegador
firefox relatorio.html
```

---

## 🐳 Usando Docker (opcional)

```bash
# Build
docker-compose build

# Executar
docker-compose up

# Acessar
curl http://localhost:8000

# Parar
docker-compose down
```

---

## 📊 Inspecionar Banco de Dados

```bash
# Instalar sqlite3 (se não tiver)
# macOS: brew install sqlite3
# Linux: apt-get install sqlite3

# Abrir banco
sqlite3 backend/anuario_audit.db

# Comandos úteis:
.tables                    # Listar tabelas
SELECT * FROM reviews;     # Ver reviews
SELECT * FROM sections;    # Ver seções
SELECT * FROM check_results LIMIT 10;  # Ver resultados
.exit                      # Sair
```

---

## 🔧 Troubleshooting

| Problema | Solução |
|----------|---------|
| `ModuleNotFoundError: No module named 'fastapi'` | Executar `pip install -r requirements.txt` |
| Porta 8000 já em uso | Mudar porta em `run.sh` ou `kill -9 $(lsof -t -i:8000)` |
| Baixar página toma muito tempo | Aumentar `REQUEST_TIMEOUT` em `app/config.py` |
| Erro ao gerar PDF | WeasyPrint é opcional. Sistema gera HTML mesmo assim |

---

## 📚 Documentação Completa

Veja `README.md` para documentação completa, modelos de dados, regras de checagem, etc.

---

## 📞 Arquivos Importantes

- `app/main.py` - Endpoints FastAPI
- `app/check_engine.py` - Regras de checagem (R1-R6)
- `app/toc_extractor.py` - Extração automática de TOC
- `app/section_extractor.py` - Extração de seções e tabelas
- `requirements.txt` - Dependências Python
- `EXAMPLES.txt` - Exemplos de curl

---

## ✅ Validação Final

Após executar `test.sh` com sucesso, você terá:

✓ Database SQLite criado
✓ TOC extraído automaticamente
✓ Checagens rodadas
✓ Relatório HTML gerado
✓ Arquivo baixado

Se tudo passou, o backend está funcionando corretamente!

---

**Pronto para usar!** 🎉
