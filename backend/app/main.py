from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
from collections import Counter

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

def extract_html_tables(html: str) -> list:
    """Extrai tabelas HTML com dados estruturados"""
    soup = BeautifulSoup(html, 'html.parser')
    tables = []
    
    for table_idx, table in enumerate(soup.find_all('table'), 1):
        # Pegar título/caption
        caption = table.find('caption')
        table_name = f"Tabela {table_idx}"
        if caption:
            caption_text = caption.get_text(strip=True)
            table_name = caption_text[:100]  # Primeiros 100 caracteres
        
        # Extrair headers
        headers = []
        thead = table.find('thead')
        if thead:
            for th in thead.find_all('th'):
                headers.append(th.get_text(strip=True))
        
        # Extrair dados
        rows = []
        tbody = table.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                cells = []
                for td in tr.find_all('td'):
                    cells.append(td.get_text(strip=True))
                if cells:
                    rows.append(cells)
        
        # Extrair fonte
        source = ""
        tfoot = table.find('tfoot')
        if tfoot:
            source = tfoot.get_text(strip=True)
        
        if rows:
            tables.append({
                "name": table_name,
                "number": table_idx,
                "headers": headers,
                "rows": rows,
                "source": source,
                "row_count": len(rows),
                "col_count": len(rows[0]) if rows else 0
            })
    
    return tables

def analyze_table_quality(table: dict) -> list:
    """Analisa qualidade e inconsistências de uma tabela"""
    issues = []
    table_name = table["name"]
    rows = table["rows"]
    headers = table["headers"]
    
    if not rows:
        return issues
    
    # 1. VERIFICAÇÃO: Células vazias
    total_cells = sum(len(row) for row in rows)
    empty_cells = sum(1 for row in rows for cell in row if not cell or cell.strip() == '')
    
    if total_cells > 0:
        empty_pct = (empty_cells / total_cells * 100)
        if empty_pct > 15:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"Muitas células vazias ({empty_pct:.1f}%)",
                "detail": f"{empty_cells} de {total_cells} células estão vazias. Sem padronização clara (zeros vs espaços).",
                "recommendation": "Padronizar células vazias - usar '0' ou 'N/A' consistentemente"
            })
    
    # 2. VERIFICAÇÃO: Inconsistência de estrutura (colunas)
    col_counts = [len(row) for row in rows]
    if len(set(col_counts)) > 1:
        issues.append({
            "severity": "FAIL",
            "table": table_name,
            "issue": f"Número de colunas inconsistente",
            "detail": f"Linhas têm de {min(col_counts)} a {max(col_counts)} colunas. Padrão: {col_counts}",
            "recommendation": "Verificar alinhamento de dados - células podem estar desalinhadas"
        })
    
    # 3. VERIFICAÇÃO: Valores duplicados (dados suspeitos)
    all_values = []
    for row in rows:
        for cell in row:
            if cell and len(cell) > 0:
                all_values.append(cell.strip())
    
    if all_values:
        value_counts = Counter(all_values)
        suspicious = {k: v for k, v in value_counts.items() if v >= 2 and any(c.isdigit() for c in k) and len(k) > 1}
        
        if suspicious:
            for val, count in list(suspicious.items())[:3]:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"Valor duplicado: '{val}' aparece {count} vezes",
                    "detail": f"Valor numérico/alfanumérico repetido {count} vezes. Estatisticamente improvável.",
                    "recommendation": "Verificar se é cópia acidental ou dado genuíno"
                })
    
    # 4. VERIFICAÇÃO: Linhas completamente duplicadas
    row_texts = [' '.join(row) for row in rows]
    row_counts = Counter(row_texts)
    dup_rows = {k: v for k, v in row_counts.items() if v > 1}
    
    if dup_rows:
        for row_text, count in list(dup_rows.items())[:2]:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"Linha duplicada {count} vezes",
                "detail": f"Padrão: '{row_text[:50]}...' aparece múltiplas vezes",
                "recommendation": "Verificar se é duplicação acidental"
            })
    
    # 5. VERIFICAÇÃO: Erros em somas/totais
    for row_idx, row in enumerate(rows):
        row_text_lower = ' '.join(row).lower()
        
        # Se parece ser uma linha de total
        if any(x in row_text_lower for x in ['total', 'soma', 'subtotal', 'consolidado']):
            # Tentar extrair números
            numbers = []
            for cell in row:
                # Extrair números (suporta vírgula e ponto como separador decimal)
                matches = re.findall(r'\d+(?:[.,]\d+)?', cell)
                for match in matches:
                    try:
                        num = float(match.replace(',', '.'))
                        numbers.append(num)
                    except:
                        pass
            
            # Se tem pelo menos 3 números, último é provavelmente o total
            if len(numbers) >= 3:
                parts = numbers[:-1]
                total = numbers[-1]
                calculated = sum(parts)
                
                if calculated > 0:
                    diff = abs(calculated - total)
                    diff_pct = (diff / calculated * 100) if calculated > 0 else 0
                    
                    if diff_pct > 0.1:  # Diferença maior que 0.1%
                        issues.append({
                            "severity": "FAIL",
                            "table": table_name,
                            "issue": f"Erro na soma (linha {row_idx+1})",
                            "detail": f"Soma dos valores: {calculated:.2f}, mas total registrado: {total:.2f}. Diferença: {diff:.2f}",
                            "recommendation": "Recalcular e corrigir o total"
                        })
    
    # 6. VERIFICAÇÃO: Valores extremos ou suspeitos
    for row_idx, row in enumerate(rows):
        for col_idx, cell in enumerate(row):
            # Verificar zeros à esquerda (00000, 000)
            if re.match(r'^0{2,}', cell) and len(cell) > 2:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"Valor com zeros à esquerda: '{cell}'",
                    "detail": f"Linha {row_idx+1}, coluna {col_idx+1}: '{cell}' pode ser placeholder",
                    "recommendation": "Verificar se é valor real ou placeholder"
                })
                break  # Uma por tabela é suficiente
    
    # 7. VERIFICAÇÃO: Mudanças drasticamente anormais em séries
    if len(rows) > 2:
        # Procurar por colunas numéricas
        numeric_cols = []
        for col_idx in range(len(rows[0])):
            numbers = []
            for row in rows:
                if col_idx < len(row):
                    match = re.findall(r'\d+(?:[.,]\d+)?', row[col_idx])
                    if match:
                        try:
                            numbers.append(float(match[0].replace(',', '.')))
                        except:
                            pass
            if len(numbers) > 2:
                numeric_cols.append((col_idx, numbers))
        
        # Verificar variações anormais
        for col_idx, numbers in numeric_cols:
            for i in range(len(numbers) - 1):
                if numbers[i] > 0 and numbers[i+1] > 0:
                    ratio = numbers[i+1] / numbers[i]
                    if ratio < 0.1 or ratio > 10:  # Variação maior que 10x ou queda de 90%
                        issues.append({
                            "severity": "WARN",
                            "table": table_name,
                            "issue": f"Variação anormal em coluna",
                            "detail": f"Valor muda de {numbers[i]:.0f} para {numbers[i+1]:.0f} (variação de {ratio:.1f}x). Típico de queda de 92% ou aumento de 10x.",
                            "recommendation": "Verificar se mudança é real ou erro de digitação"
                        })
                        break  # Uma por tabela
    
    # 8. VERIFICAÇÃO: Falta de fonte
    if not table["source"] or "Fonte" not in table["source"]:
        issues.append({
            "severity": "WARN",
            "table": table_name,
            "issue": "Fonte não identificada",
            "detail": "Tabela não possui referência de origem dos dados",
            "recommendation": "Adicionar identificação da fonte dos dados"
        })
    
    return issues

def run_real_audit(html: str, report_year: int, base_year: int) -> list:
    """Auditoria completa com análise real de tabelas HTML"""
    issues = []
    
    # Extrair tabelas
    tables = extract_html_tables(html)
    
    if not tables:
        issues.append({
            "severity": "INFO",
            "table": "Documento",
            "issue": "Nenhuma tabela estruturada encontrada",
            "detail": "Não foram localizadas tags <table> no documento",
            "recommendation": "Verificar se os dados estão em outro formato"
        })
        return issues
    
    # Analisar cada tabela
    for table in tables:
        table_issues = analyze_table_quality(table)
        issues.extend(table_issues)
    
    # Informação: quantas tabelas encontradas
    issues.insert(0, {
        "severity": "PASS",
        "table": "Documento",
        "issue": f"Encontradas {len(tables)} tabela(s) estruturada(s)",
        "detail": f"Análise executada em {len(tables)} tabelas HTML",
        "recommendation": "Verificar inconsistências detectadas abaixo"
    })
    
    # Verificações globais
    year_str = str(report_year)
    if year_str in html:
        count = html.count(year_str)
        issues.append({
            "severity": "PASS",
            "table": "Metadados",
            "issue": f"Ano {year_str} está presente",
            "detail": f"Referência ao ano aparece {count} vezes no documento",
            "recommendation": "OK"
        })
    else:
        issues.append({
            "severity": "WARN",
            "table": "Metadados",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "Não foi localizada referência ao ano de relatório",
            "recommendation": f"Adicionar ano {year_str} no documento"
        })
    
    return issues

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
        button { width: 100%; padding: 12px; background: linear-gradient(135deg, #003366 0%, #2E1D86 100%); color: white; border: none; border-radius: 6px; font-weight: 600; cursor: pointer; margin-top: 20px; }
        #loading { display: none; text-align: center; padding: 20px; }
        .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #003366; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 15px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .results { display: none; margin-top: 30px; }
        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 30px; }
        .stat { text-align: center; padding: 20px; background: #f8f9fa; border-radius: 6px; }
        .stat-number { font-size: 32px; font-weight: bold; color: #003366; }
        .issue-item { padding: 20px; margin-bottom: 15px; border-radius: 8px; border-left: 5px solid; }
        .issue-item.pass { background: #e8f5e9; border-left-color: #4caf50; }
        .issue-item.warn { background: #fff3e0; border-left-color: #ff9800; }
        .issue-item.fail { background: #ffebee; border-left-color: #f44336; }
        .issue-item.info { background: #e3f2fd; border-left-color: #2196f3; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; color: white; margin-left: 10px; }
        .badge-pass { background: #4caf50; }
        .badge-warn { background: #ff9800; }
        .badge-fail { background: #f44336; }
        .badge-info { background: #2196f3; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <p class="subtitle">Análise detalhada de inconsistências em tabelas</p>

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
            <button onclick="audit()">🔍 Executar Auditoria Completa</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Analisando tabelas...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 style="color: #003366; margin-bottom: 20px;">Discrepâncias que merecem atenção:</h2>
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
            if (!issues) issues = [];

            const pass = issues.filter(i => i.severity === 'PASS').length;
            const warn = issues.filter(i => i.severity === 'WARN').length;
            const fail = issues.filter(i => i.severity === 'FAIL').length;

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
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <strong style="color: #333;">${i.table}</strong>
                        <span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span>
                    </div>
                    <div style="color: #333; font-weight: 500; margin-bottom: 8px; font-size: 15px;">${i.issue}</div>
                    <div style="color: #555; font-size: 14px; margin-bottom: 10px; line-height: 1.5;">${i.detail}</div>
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

@app.get("/", response_class=HTMLResponse)
def serve():
    return HTML

@app.post("/audit")
def audit(req: AuditRequest):
    try:
        resp = requests.get(req.url, timeout=20)
        html = resp.text
        
        issues = run_real_audit(html, req.report_year, req.base_year)
        
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