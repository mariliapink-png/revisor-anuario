# Auditoria Anuário Estatístico UnB - Backend MVP

Sistema automatizado para auditoria do Anuário Estatístico da UnB publicado como site HTML.

## 🎯 Objetivo

Criar um backend que:
1. Descobre automaticamente seções via TOC/menu do site
2. Roda checagens automáticas por seção (6 regras de qualidade)
3. Salva resultados no SQLite
4. Exporta relatório consolidado em HTML/PDF

## 📋 Requisitos Cumpridos

- ✅ Python 3.11+
- ✅ FastAPI + Uvicorn
- ✅ BeautifulSoup4 (extração HTML)
- ✅ pandas + lxml (análise de tabelas)
- ✅ SQLite + SQLAlchemy (persistência)
- ✅ Jinja2 (geração de relatórios)
- ✅ WeasyPrint (PDF opcional)

## 🚀 Quick Start

### 1. Instalar dependências

```bash
cd backend
python -m venv venv

# Linux/macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Iniciar servidor

```bash
python app/main.py
```

Ou:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

O servidor estará disponível em: **http://localhost:8000**

## 📚 Uso da API

### 1. Criar Review (com extração automática de TOC)

```bash
curl -X POST "http://localhost:8000/reviews" \
  -H "Content-Type: application/json" \
  -d '{
    "start_url": "https://anuariounb2025.netlify.app/",
    "report_year": 2025,
    "base_year": 2024
  }'
```

**Resposta:**
```json
{
  "id": 1,
  "start_url": "https://anuariounb2025.netlify.app/",
  "report_year": 2025,
  "base_year": 2024,
  "created_at": "2025-02-18T10:30:00"
}
```

**O que acontece:**
- Sistema baixa a página inicial
- Identifica o container TOC (nav/aside/div com mais links internos)
- Extrai todas as seções (title, url, anchor, level)
- Salva no banco de dados
- Retorna ID da review para uso posterior

### 2. Listar Seções da Review

```bash
curl "http://localhost:8000/reviews/1/sections"
```

**Resposta:**
```json
[
  {
    "id": 1,
    "review_id": 1,
    "title": "Apresentação",
    "url": "https://anuariounb2025.netlify.app/index.html",
    "anchor": null,
    "level": 1,
    "is_virtual": false
  },
  {
    "id": 2,
    "review_id": 1,
    "title": "Dados Gerais",
    "url": "https://anuariounb2025.netlify.app/dados.html",
    "anchor": "#dados-gerais",
    "level": 2,
    "is_virtual": false
  }
  ...
]
```

### 3. Rodar Checagens para uma Seção

```bash
curl -X POST "http://localhost:8000/reviews/1/sections/1/run-checks"
```

**Resposta:**
```json
{
  "id": 1,
  "mode": "section",
  "started_at": "2025-02-18T10:31:00",
  "finished_at": "2025-02-18T10:31:05",
  "results": [
    {
      "id": 1,
      "rule": "R1_wrong_anuario_year",
      "severity": "FAIL",
      "message": "Anuário deve ser 2025, encontrado 2024",
      "evidence_json": {
        "text_snippet": "...Anuário Estatístico 2024...",
        "url": "https://anuariounb2025.netlify.app/index.html",
        "anchor": ""
      }
    }
  ]
}
```

### 4. Obter Resultados de uma Seção

```bash
curl "http://localhost:8000/reviews/1/sections/1/results"
```

### 5. Rodar Checagens para Todas as Páginas

```bash
curl -X POST "http://localhost:8000/reviews/1/run-all?max_pages=50"
```

**Comportamento:**
- Extrai URLs únicas (deduplica por URL, ignora anchors)
- Limita a 50 páginas (configurável)
- Executa em background
- Retorna status imediatamente

### 6. Salvar Revisão Manual

```bash
curl -X POST "http://localhost:8000/reviews/1/sections/1/manual" \
  -H "Content-Type: application/json" \
  -d '{
    "items_checked_json": {
      "gramatica": true,
      "dados_verificados": true
    },
    "comments": "Seção revisada manualmente. Sem problemas encontrados.",
    "reviewer": "João Silva"
  }'
```

### 7. Exportar Relatório

```bash
# HTML
curl "http://localhost:8000/reviews/1/export?format=html"

# PDF (se WeasyPrint disponível)
curl "http://localhost:8000/reviews/1/export?format=pdf"
```

**Resposta:**
```json
{
  "message": "Relatório gerado",
  "filename": "report_review_1_20250218_103145.html",
  "download_url": "/downloads/report_review_1_20250218_103145.html"
}
```

### 8. Baixar Arquivo

```bash
curl "http://localhost:8000/downloads/report_review_1_20250218_103145.html" \
  -o relatorio.html
```

## 📋 Regras de Checagem Implementadas

### R1: Year Checks (Verificação de Anos)
- **FAIL**: Encontra "Anuário Estatístico YYYY" com ano incorreto
- **FAIL**: Detecta anos inválidos (ex: "20234")
- **WARN**: Encontra base_year-1 mas não encontra base_year
- **FAIL**: Série histórica truncada (ex: "2020 a 2023" quando base_year=2024)

### R2: Decimal Separator (Separador Decimal)
- **WARN**: Detecta decimal com ponto (15.84) em vez de vírgula (15,84)
- Heurística: ignora padrões que são obviamente milhares (ex: 1.769.277)

### R3: Table Source Required (Fonte em Tabelas)
- **FAIL**: Tabela sem "Fonte:" detectável em caption ou notas

### R4: Table Totals (Validação de Totais)
- **FAIL**: Linha/coluna "Total" com somas que não conferem
- Compara valor informado vs calculado

### R5: Table Completeness (Integridade de Tabelas)
- **WARN**: Detecta células vazias
- **FAIL**: Encontra "ND" sem explicação em notas
- **FAIL**: Tabela não consegue ser parseada

### R6: Total Row Style (Estilo da Linha Total)
- **WARN**: Linha "Total" sem destaque visual (background/font-weight/<strong>/<b>)

## 📊 Modelo de Dados (SQLite)

```sql
-- Reviews
CREATE TABLE reviews (
  id INTEGER PRIMARY KEY,
  start_url VARCHAR UNIQUE,
  report_year INTEGER,
  base_year INTEGER,
  created_at DATETIME
);

-- Seções extraídas do TOC
CREATE TABLE sections (
  id INTEGER PRIMARY KEY,
  review_id INTEGER FOREIGN KEY,
  title VARCHAR,
  url VARCHAR,
  anchor VARCHAR NULL,
  level INTEGER,
  is_virtual BOOLEAN
);

-- Execuções de checagem
CREATE TABLE check_runs (
  id INTEGER PRIMARY KEY,
  review_id INTEGER FOREIGN KEY,
  section_id INTEGER FOREIGN KEY,
  mode VARCHAR, -- "section" ou "page"
  started_at DATETIME,
  finished_at DATETIME NULL
);

-- Resultados de checagem
CREATE TABLE check_results (
  id INTEGER PRIMARY KEY,
  checkrun_id INTEGER FOREIGN KEY,
  rule VARCHAR,
  severity VARCHAR, -- "PASS", "WARN", "FAIL"
  message VARCHAR,
  evidence_json JSON NULL
);

-- Revisões manuais
CREATE TABLE manual_reviews (
  id INTEGER PRIMARY KEY,
  review_id INTEGER FOREIGN KEY,
  section_id INTEGER FOREIGN KEY,
  items_checked_json JSON NULL,
  comments TEXT NULL,
  reviewer VARCHAR NULL,
  updated_at DATETIME
);
```

## 🔍 Descoberta Automática de TOC

O sistema usa uma heurística robusta para localizar o Table of Contents:

1. **Procura por tags óbvias**: `<nav>`, `<aside>`
2. **Procura por classes sugestivas**: toc, menu, sidebar, nav, index
3. **Heurística de contagem**: escolhe elemento com maior número de `<a>` internos (mesmo domínio)
4. **Normalização**: converte URLs relativas para absolutas, extrai anchors
5. **Filtragem**: apenas links para o mesmo domínio
6. **Inferência de nível**: conta `<ul>` ancestrais para determinar profundidade

## 📄 Extração de Seções

Para cada seção:

1. **Download da URL**: faz request com User-Agent
2. **Isolamento por âncora**: se existe anchor, extrai bloco até próximo header
3. **Extração de texto**: todo o conteúdo textual
4. **Extração de tabelas**: 
   - Parse com `pandas.read_html(decimal=",", thousands=".")`
   - Captura caption, HTML, notas, fonte
5. **Detecção de fonte**: procura "Fonte:" em caption ou notas

## 📦 Estrutura de Diretórios

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # Endpoints FastAPI
│   ├── config.py               # Configurações
│   ├── models.py               # Modelos SQLAlchemy
│   ├── database.py             # Setup SQLite
│   ├── schemas.py              # Schemas Pydantic
│   ├── toc_extractor.py        # Extração de TOC
│   ├── section_extractor.py    # Extração de seções
│   ├── check_engine.py         # Regras R1-R6
│   └── report_generator.py     # Geração HTML/PDF
├── exports/
│   └── downloads/              # Arquivos gerados
├── templates/
│   └── report.html             # Template Jinja2
├── anuario_audit.db            # Banco SQLite
├── requirements.txt
└── README.md
```

## 🧪 Teste com URL Fornecida

### Caso de Teste Esperado

**URL:** https://anuariounb2025.netlify.app/
**report_year:** 2025
**base_year:** 2024

**Falhas Esperadas (segundo especificação):**
- ❌ "Anuário Estatístico 2024" na Apresentação → **R1 FAIL**
- ❌ "20234" no sumário → **R1 FAIL**

### Executar

```bash
# 1. Criar review
curl -X POST "http://localhost:8000/reviews" \
  -H "Content-Type: application/json" \
  -d '{
    "start_url": "https://anuariounb2025.netlify.app/",
    "report_year": 2025,
    "base_year": 2024
  }'

# Response: { "id": 1, ... }

# 2. Aguardar um pouco pela extração do TOC

# 3. Listar seções (confirmar se foi extraído a Apresentação)
curl "http://localhost:8000/reviews/1/sections"

# 4. Rodar checagens na primeira seção (Apresentação)
curl -X POST "http://localhost:8000/reviews/1/sections/1/run-checks"

# 5. Exportar relatório
curl "http://localhost:8000/reviews/1/export?format=html"

# 6. Baixar relatório
curl "http://localhost:8000/downloads/report_review_1_XXXXXX.html" -o relatorio.html
```

## 🔧 Troubleshooting

### Erro: "ModuleNotFoundError: No module named 'app'"

Certifique-se que está rodando do diretório `backend`:
```bash
cd backend
python app/main.py
```

### Erro: Banco de dados não encontrado

Será criado automaticamente na primeira execução em `backend/anuario_audit.db`

### WeasyPrint não funciona

Sistema continuará gerando HTML normalmente. PDF é opcional:
- Se weasyprint não está disponível, apenas HTML será exportado
- Para usar PDF: `pip install weasyprint`

### Timeout ao baixar página

Aumentar timeout em `app/config.py`:
```python
REQUEST_TIMEOUT = 60  # aumentar de 30
```

## 📝 Exemplos Práticos

### Exemplo 1: Review Completa com Relatório

```bash
#!/bin/bash

# 1. Criar review
REVIEW=$(curl -s -X POST "http://localhost:8000/reviews" \
  -H "Content-Type: application/json" \
  -d '{
    "start_url": "https://anuariounb2025.netlify.app/",
    "report_year": 2025,
    "base_year": 2024
  }')

REVIEW_ID=$(echo $REVIEW | jq -r '.id')
echo "Review criada: $REVIEW_ID"

# 2. Aguardar um pouco
sleep 3

# 3. Rodar checagens em todas as páginas
curl -s -X POST "http://localhost:8000/reviews/$REVIEW_ID/run-all?max_pages=50"

# 4. Aguardar conclusão
sleep 10

# 5. Exportar relatório
EXPORT=$(curl -s "http://localhost:8000/reviews/$REVIEW_ID/export?format=html")
FILENAME=$(echo $EXPORT | jq -r '.filename')
echo "Relatório: $FILENAME"

# 6. Baixar
curl -s "http://localhost:8000/downloads/$FILENAME" -o relatorio.html
echo "Relatório baixado: relatorio.html"
```

## 📞 Support & Debugging

### Logs

Verifique os logs do servidor para debug:
```
INFO:app.main:Review criada: 1 para https://anuariounb2025.netlify.app/
INFO:app.toc_extractor:Extraído 15 seções do TOC
INFO:app.main:Checagens executadas para seção 1: 3 resultados
```

### Database

Inspecionar dados salvos:
```bash
sqlite3 backend/anuario_audit.db

# Listar reviews
SELECT * FROM reviews;

# Listar seções
SELECT * FROM sections WHERE review_id=1;

# Listar resultados
SELECT * FROM check_results;
```

## 📄 Licença

Projeto da UnB - Decanato de Planejamento, Orçamento e Avaliação Institucional (DPO)

## 🤝 Contribuições

Melhorias e sugestões são bem-vindas!
