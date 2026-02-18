from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
import re

app = FastAPI(title="Auditoria Anuário UnB")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AuditRequest(BaseModel):
    url: str
    report_year: int
    base_year: int

def extract_all_table_structures(html: str) -> List[dict]:
    """Extrai QUALQUER estrutura de tabela - HTML, divs, pre, etc"""
    
    soup = BeautifulSoup(html, 'html.parser')
    tables = []
    
    # 1. Tabelas HTML tradicionais
    for idx, table in enumerate(soup.find_all('table'), 1):
        rows = []
        for tr in table.find_all('tr'):
            cells = []
            for td in tr.find_all(['td', 'th']):
                cells.append(td.get_text(strip=True))
            if cells:
                rows.append(cells)
        
        if rows:
            tables.append({
                "type": "HTML_TABLE",
                "number": idx,
                "name": f"Tabela {idx}",
                "rows": rows,
                "row_count": len(rows),
                "col_count": len(rows[0]) if rows else 0
            })
    
    # 2. Estruturas em <pre> (dados formatados do R)
    for idx, pre in enumerate(soup.find_all('pre'), 1):
        text = pre.get_text()
        lines = text.strip().split('\n')
        
        # Se tem múltiplas linhas com números, pode ser tabela
        if len(lines) > 3:
            rows = []
            for line in lines:
                # Dividir por espaços múltiplos
                cells = [c.strip() for c in re.split(r'\s{2,}', line.strip()) if c.strip()]
                if cells:
                    rows.append(cells)
            
            if len(rows) > 1:
                tables.append({
                    "type": "PRE_FORMAT",
                    "number": idx,
                    "name": f"Dados formatados {idx} (em <pre>)",
                    "rows": rows,
                    "row_count": len(rows),
                    "col_count": len(rows[0]) if rows else 0
                })
    
    # 3. Divs com classe "table" ou "data"
    for idx, div in enumerate(soup.find_all('div', class_=re.compile(r'table|data', re.I)), 1):
        text = div.get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        if len(lines) > 3:
            tables.append({
                "type": "DIV_TABLE",
                "number": idx,
                "name": f"Dados em div {idx}",
                "rows": [[cell for cell in lines]],  # Aproximado
                "row_count": len(lines),
                "col_count": 1
            })
    
    # 4. Procurar por blockquote (às vezes usado para tabelas)
    for idx, bq in enumerate(soup.find_all('blockquote'), 1):
        text = bq.get_text()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        if len(lines) > 3:
            tables.append({
                "type": "BLOCKQUOTE",
                "number": idx,
                "name": f"Bloco de dados {idx}",
                "rows": [[cell for cell in lines]],
                "row_count": len(lines),
                "col_count": 1
            })
    
    return tables

def analyze_table_content(table: dict) -> List[dict]:
    """Analisa conteúdo de uma tabela para inconsistências"""
    issues = []
    rows = table["rows"]
    table_name = table["name"]
    
    if not rows:
        return issues
    
    # 1. Células vazias
    total_cells = sum(len(row) for row in rows)
    empty_cells = sum(1 for row in rows for cell in row if not cell or cell.strip() == '')
    
    if total_cells > 0:
        empty_pct = (empty_cells / total_cells * 100)
        if empty_pct > 20:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"{empty_pct:.1f}% de células vazias",
                "detail": f"{empty_cells} de {total_cells} células estão vazias",
                "recommendation": "Verificar se faltam dados ou se é intencional"
            })
    
    # 2. Valores duplicados
    all_values = []
    for row in rows:
        for cell in row:
            if cell and cell.strip():
                all_values.append(cell.strip())
    
    from collections import Counter
    value_counts = Counter(all_values)
    duplicates = {k: v for k, v in value_counts.items() if v > 2 and any(c.isdigit() for c in k)}
    
    if duplicates:
        for val, count in list(duplicates.items())[:3]:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"Valor '{val}' repetido {count} vezes",
                "detail": f"Valor aparece {count} vezes em diferentes células",
                "recommendation": "Verificar se é dado duplicado acidentalmente"
            })
    
    # 3. Linhas duplicadas
    row_sigs = []
    for row in rows:
        sig = tuple(row[:min(3, len(row))])
        row_sigs.append(sig)
    
    sig_counts = Counter(row_sigs)
    dup_sigs = {k: v for k, v in sig_counts.items() if v > 1}
    
    if dup_sigs:
        for sig, count in list(dup_sigs.items())[:2]:
            sig_str = ' | '.join(str(s)[:30] for s in sig)
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"Linha duplicada {count} vezes",
                "detail": f"Padrão: {sig_str}",
                "recommendation": "Verificar se é duplicação acidental"
            })
    
    # 4. Inconsistência de colunas
    col_counts = [len(row) for row in rows]
    if len(set(col_counts)) > 1:
        issues.append({
            "severity": "WARN",
            "table": table_name,
            "issue": f"Número de colunas inconsistente",
            "detail": f"Linhas têm {min(col_counts)}-{max(col_counts)} colunas",
            "recommendation": "Padronizar número de colunas"
        })
    
    # 5. Análise numérica - somas
    for row_idx, row in enumerate(rows):
        row_text = ' '.join(row)
        
        if any(x in row_text.lower() for x in ['total', 'soma', 'subtotal']):
            # Extrair números
            numbers = [float(re.sub(r'[^\d.]', '', x)) for x in row if re.search(r'\d', x)]
            
            if len(numbers) >= 3:
                parts = numbers[:-1]
                total = numbers[-1]
                calculated = sum(parts)
                
                if calculated > 0 and abs(calculated - total) > 0.01:
                    issues.append({
                        "severity": "FAIL",
                        "table": table_name,
                        "issue": f"Erro na soma (linha {row_idx+1})",
                        "detail": f"Soma dos valores: {calculated:.0f}, mas total: {total:.0f}",
                        "recommendation": "Recalcular a soma"
                    })
    
    return issues

def run_audit(html: str, report_year: int, base_year: int) -> List[dict]:
    """Auditoria completa adaptada para RMarkdown"""
    issues = []
    
    # Extrair todas as estruturas de tabela
    tables = extract_all_table_structures(html)
    
    if not tables:
        issues.append({
            "severity": "INFO",
            "table": "Documento",
            "issue": "Analisando estrutura do documento",
            "detail": f"Documento HTML detectado. Procurando por estruturas de dados...",
            "recommendation": "Verificar se o documento contém dados tabulares"
        })
        return issues
    
    # Analisar cada tabela
    for table in tables:
        table_issues = analyze_table_content(table)
        issues.extend(table_issues)
    
    # Verificações globais
    year_str = str(report_year)
    if year_str not in html:
        issues.append({
            "severity": "WARN",
            "table": "Documento",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "O ano de referência não aparece no documento",
            "recommendation": f"Verificar se {year_str} está presente"
        })
    
    # Se não houver issues, retornar OK
    if not issues:
        issues.append({
            "severity": "PASS",
            "table": "Análise Completa",
            "issue": "Documento validado",
            "detail": f"Encontradas {len(tables)} estrutura(s) de dados. Sem inconsistências críticas.",
            "recommendation": "Continuar monitorando"
        })
    
    return issues

# ===== HTML INTERFACE =====
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria UnB</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #003366 0%, #2E1D86 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 1000px;
            margin: 0 auto;
            padding: 40px;
        }
        h1 { color: #003366; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #003366; font-weight: 600; margin-bottom: 8px; }
        input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #003366 0%, #2E1D86 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 20px;
        }
        #loading { display: none; text-align: center; padding: 20px; }
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #003366;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .results { display: none; margin-top: 30px; }
        .stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat { text-align: center; padding: 20px; background: #f8f9fa; border-radius: 6px; }
        .stat-number { font-size: 32px; font-weight: bold; color: #003366; }
        .issue-item {
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            border-left: 5px solid;
        }
        .issue-item.pass { background: #e8f5e9; border-left-color: #4caf50; }
        .issue-item.warn { background: #fff3e0; border-left-color: #ff9800; }
        .issue-item.fail { background: #ffebee; border-left-color: #f44336; }
        .issue-item.info { background: #e3f2fd; border-left-color: #2196f3; }
        .issue-title { font-weight: 600; color: #333; margin-bottom: 8px; }
        .issue-table { display: inline-block; background: rgba(0,0,0,0.05); padding: 4px 10px; border-radius: 4px; font-size: 12px; margin-bottom: 10px; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        .badge-pass { background: #4caf50; color: white; }
        .badge-warn { background: #ff9800; color: white; }
        .badge-fail { background: #f44336; color: white; }
        .badge-info { background: #2196f3; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <p class="subtitle">Análise de inconsistências em documentos HTML/RMarkdown</p>

        <div id="form">
            <div class="form-group">
                <label>URL do Anuário</label>
                <input type="url" id="url" value="https://anuariounb2025.netlify.app/">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Ano Relatório</label>
                    <input type="number" id="year" value="2025">
                </div>
                <div class="form-group">
                    <label>Ano Base</label>
                    <input type="number" id="baseYear" value="2024">
                </div>
            </div>
            <button onclick="audit()">🔍 Executar Auditoria</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Analisando documento...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 style="color: #003366; margin-bottom: 20px;">Relatório de Inconsistências</h2>
            <div id="content"></div>
        </div>
    </div>

    <script>
        async function audit() {
            const url = document.getElementById('url').value;
            const year = parseInt(document.getElementById('year').value);
            const base = parseInt(document.getElementById('baseYear').value);

            document.getElementById('form').style.display = 'none';
            document.getElementById('loading').style.display = 'block';

            try {
                const res = await fetch('https://revisor-anuario-2.onrender.com/audit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, report_year: year, base_year: base })
                });

                const data = await res.json();
                showResults(data.issues);
            } catch (e) {
                alert('Erro: ' + e.message);
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
            }
        }

        function showResults(issues) {
            if (!issues || issues.length === 0) {
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
                return;
            }

            const pass = issues.filter(i => i.severity === 'PASS').length;
            const warn = issues.filter(i => i.severity === 'WARN').length;
            const fail = issues.filter(i => i.severity === 'FAIL').length;
            const info = issues.filter(i => i.severity === 'INFO').length;

            document.getElementById('stats').innerHTML = `
                <div class="stat">
                    <div class="stat-number" style="color: #4caf50;">${pass}</div>
                    <div class="stat-label">OK</div>
                </div>
                <div class="stat">
                    <div class="stat-number" style="color: #ff9800;">${warn}</div>
                    <div class="stat-label">Avisos</div>
                </div>
                <div class="stat">
                    <div class="stat-number" style="color: #f44336;">${fail}</div>
                    <div class="stat-label">Erros</div>
                </div>
            `;

            document.getElementById('content').innerHTML = issues.map(i => `
                <div class="issue-item ${i.severity.toLowerCase()}">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                        <span class="issue-table">${i.table}</span>
                        <span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span>
                    </div>
                    <div class="issue-title">${i.issue}</div>
                    <div style="color: #555; font-size: 14px; margin-bottom: 10px;">${i.detail}</div>
                    <div style="color: #666; font-style: italic; font-size: 13px; padding: 10px; background: rgba(0,0,0,0.03); border-radius: 4px;">
                        💡 ${i.recommendation}
                    </div>
                </div>
            `).join('');

            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }
    </script>
</body>
</html>"""

# ===== ENDPOINTS =====

@app.get("/", response_class=HTMLResponse)
def serve():
    return HTML

@app.post("/audit")
def audit(req: AuditRequest):
    try:
        resp = requests.get(req.url, timeout=20)
        html = resp.text
        
        issues = run_audit(html, req.report_year, req.base_year)
        
        return {
            "status": "ok",
            "issues": issues
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)