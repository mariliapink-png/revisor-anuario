from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Tuple

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

# ===== DOWNLOAD E DIAGNÓSTICO =====

def download_page(url: str) -> Tuple[str, Dict]:
    """Baixa HTML com headers realistas e registra diagnóstico"""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
    }
    
    try:
        resp = requests.get(url, timeout=20, headers=headers)
        resp.encoding = 'utf-8'
        html = resp.text
        
        soup = BeautifulSoup(html, 'html.parser')
        tables = soup.find_all('table')
        
        diagnostics = {
            "tamanho_html_kb": len(html) / 1024,
            "contagem_tables": len(tables),
            "primeiros_300_chars": html[:300],
            "status": "OK"
        }
        
        return html, diagnostics
    except Exception as e:
        return "", {
            "tamanho_html_kb": 0,
            "contagem_tables": 0,
            "primeiros_300_chars": "",
            "status": f"ERRO: {str(e)}"
        }

# ===== EXTRAÇÃO DE TABELAS =====

def extract_tables_from_html(html: str, url: str) -> List[Dict]:
    """Extrai tabelas HTML completas com caption e fonte"""
    
    tables = []
    soup = BeautifulSoup(html, 'html.parser')
    
    for table_idx, table_elem in enumerate(soup.find_all('table'), 1):
        # 1. Caption/Título
        caption = table_elem.find('caption')
        table_name = f"Tabela {table_idx}"
        if caption:
            table_name = caption.get_text(strip=True)
        
        # 2. Headers
        headers = []
        thead = table_elem.find('thead')
        if thead:
            for th in thead.find_all('th'):
                headers.append(th.get_text(strip=True))
        
        # 3. Dados
        rows = []
        tbody = table_elem.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                cells = []
                for td in tr.find_all('td'):
                    cells.append(td.get_text(strip=True))
                if cells:
                    rows.append(cells)
        
        # 4. Fonte e Notas (procura nos 10 próximos siblings)
        fonte = ""
        nota = ""
        next_elem = table_elem.find_next()
        elem_count = 0
        
        while next_elem and elem_count < 10:
            text = next_elem.get_text(strip=True) if next_elem else ""
            if 'Fonte:' in text:
                fonte = text
            if 'Nota:' in text or 'Notas:' in text:
                nota = text
            
            # Para se achar outro header ou tabela
            if next_elem.name in ['h1', 'h2', 'h3', 'table']:
                break
            
            next_elem = next_elem.find_next() if next_elem else None
            elem_count += 1
        
        # 5. Armazenar tabela
        if rows:
            tables.append({
                "numero": table_idx,
                "nome": table_name,
                "headers": headers,
                "rows": rows,
                "fonte": fonte,
                "nota": nota,
                "html": str(table_elem)
            })
    
    return tables

# ===== ANÁLISE DE TABELAS =====

def analyze_table(table: Dict, report_year: int, base_year: int) -> List[Dict]:
    """Analisa qualidade de uma tabela"""
    
    issues = []
    table_name = table["nome"]
    rows = table["rows"]
    
    if not rows:
        return issues
    
    # 1. CHECK: Fonte obrigatória
    if not table["fonte"]:
        issues.append({
            "severity": "FAIL",
            "table": table_name,
            "issue": "Fonte não identificada",
            "detail": "Tabela sem referência clara de origem dos dados",
            "recommendation": "Adicionar 'Fonte: [origem]' abaixo da tabela"
        })
    
    # 2. CHECK: Células vazias (ND sem nota)
    total_cells = sum(len(row) for row in rows)
    empty_cells = sum(1 for row in rows for cell in row if not cell.strip())
    
    if total_cells > 0:
        empty_pct = (empty_cells / total_cells * 100)
        if empty_pct > 15:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"{empty_pct:.1f}% de células vazias",
                "detail": f"{empty_cells}/{total_cells} células. Verificar se são '0', 'ND', ou dados faltantes.",
                "recommendation": "Padronizar: usar '0', 'ND' com nota, ou 'N/A' de forma consistente"
            })
    
    # 3. CHECK: Inconsistência de colunas
    col_counts = [len(row) for row in rows]
    if len(set(col_counts)) > 1:
        issues.append({
            "severity": "FAIL",
            "table": table_name,
            "issue": "Número de colunas inconsistente",
            "detail": f"Linhas têm {min(col_counts)}-{max(col_counts)} colunas",
            "recommendation": "Verificar alinhamento: células podem estar desalinhadas"
        })
    
    # 4. CHECK: Totais (recalcular)
    for row_idx, row in enumerate(rows):
        row_text = ' '.join(row).lower()
        
        if 'total' in row_text or 'soma' in row_text or 'subtotal' in row_text:
            # Extrair números
            numbers = []
            for cell in row:
                matches = re.findall(r'\d+(?:[.,]\d+)?', cell)
                for m in matches:
                    try:
                        num = float(m.replace(',', '.'))
                        numbers.append(num)
                    except:
                        pass
            
            if len(numbers) >= 3:
                parts = numbers[:-1]
                total = numbers[-1]
                calc_sum = sum(parts)
                
                if calc_sum > 0 and abs(calc_sum - total) > 0.01:
                    issues.append({
                        "severity": "FAIL",
                        "table": table_name,
                        "issue": f"Erro na soma (linha {row_idx+1})",
                        "detail": f"Soma dos parciais: {calc_sum:.2f}; total registrado: {total:.2f}. Diferença: {abs(calc_sum - total):.2f}",
                        "recommendation": "Recalcular e corrigir o total"
                    })
    
    # 5. CHECK: Formatação decimal
    all_text = ' '.join([' '.join(row) for row in rows])
    comma_nums = len(re.findall(r'\d{1,3},\d{2,}', all_text))
    dot_nums = len(re.findall(r'\d+\.\d+', all_text))
    
    if comma_nums > 0 and dot_nums > 5:  # Mais de 5 pontos = provavelmente milhares
        issues.append({
            "severity": "WARN",
            "table": table_name,
            "issue": "Mistura de separadores decimais",
            "detail": f"{comma_nums} números com vírgula, {dot_nums} com ponto",
            "recommendation": "Padronizar separador decimal (usar vírgula para decimais, ponto para milhares)"
        })
    
    # 6. CHECK: Linha de Total com destaque (estilo)
    has_total = any('total' in ' '.join(row).lower() for row in rows)
    if has_total:
        # Verificar se há estilo de destaque no HTML
        if '<tfoot>' not in table["html"] and '<strong>' not in table["html"].lower():
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": "Linha de Total sem destaque visual",
                "detail": "Total não está em <tfoot> nem marcado como <strong> ou <b>",
                "recommendation": "Destaque a linha de total com formatação (negrito, cor, ou <tfoot>)"
            })
    
    return issues

# ===== CHECKS GLOBAIS =====

def check_year(html: str, report_year: int, base_year: int) -> List[Dict]:
    """Verifica consistência de anos"""
    
    issues = []
    year_str = str(report_year)
    base_str = str(base_year)
    
    # Detectar erros óbvios
    if f"20234" in html or f"202{report_year-1}" in html:
        issues.append({
            "severity": "FAIL",
            "table": "Metadados",
            "issue": "Possível erro de digitação no ano",
            "detail": f"Encontrado ano digitado incorretamente",
            "recommendation": f"Verificar e corrigir para {year_str}"
        })
    
    # Verificar se ano aparece
    if year_str not in html:
        issues.append({
            "severity": "FAIL",
            "table": "Metadados",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "Documento não referencia o ano de relatório",
            "recommendation": f"Adicionar '{year_str}' em títulos/captions"
        })
    
    # Verificar série truncada
    if f"{base_year} a {report_year-1}" in html and year_str not in html:
        issues.append({
            "severity": "FAIL",
            "table": "Metadados",
            "issue": "Série truncada",
            "detail": f"Série mostra '{base_year} a {report_year-1}' mas deveria incluir {year_str}",
            "recommendation": f"Estender série para incluir {year_str}"
        })
    
    return issues

# ===== ORQUESTRAÇÃO PRINCIPAL =====

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    """Auditoria completa"""
    
    issues = []
    
    # 1. Download com diagnóstico
    html, diag = download_page(url)
    
    if diag["contagem_tables"] == 0:
        issues.append({
            "severity": "FAIL",
            "table": "Documento",
            "issue": "Nenhuma tabela HTML encontrada",
            "detail": f"Diagnóstico: {diag['status']} | Tamanho: {diag['tamanho_html_kb']:.1f} KB",
            "recommendation": "Verificar renderização (possível conteúdo dinâmico/JavaScript). Considerar Playwright."
        })
        return issues
    
    # Informar sucesso
    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "issue": f"✓ {diag['contagem_tables']} tabela(s) encontrada(s)",
        "detail": f"HTML: {diag['tamanho_html_kb']:.1f} KB",
        "recommendation": "Analisando tabelas..."
    })
    
    # 2. Extrair tabelas
    tables = extract_tables_from_html(html, url)
    
    # 3. Analisar cada tabela
    for table in tables:
        table_issues = analyze_table(table, report_year, base_year)
        issues.extend(table_issues)
    
    # 4. Checks globais
    year_issues = check_year(html, report_year, base_year)
    issues.extend(year_issues)
    
    # Se nenhum problema encontrado
    if len([i for i in issues if i["severity"] in ["FAIL", "WARN"]]) == 1:  # Só a msg de sucesso
        issues.append({
            "severity": "PASS",
            "table": "Análise Completa",
            "issue": "Auditoria concluída - Sem problemas críticos",
            "detail": f"Verificadas {len(tables)} tabela(s). Nenhuma inconsistência crítica detectada.",
            "recommendation": "Documento está adequado"
        })
    
    return issues

# ===== API =====

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
        .badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; color: white; margin-left: 10px; }
        .badge-pass { background: #4caf50; }
        .badge-warn { background: #ff9800; }
        .badge-fail { background: #f44336; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <p class="subtitle">Análise de qualidade e consistência de dados</p>

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
            <p>Auditando documento...</p>
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
                    <div style="color: #333; font-weight: 500; margin-bottom: 8px;">${i.issue}</div>
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

@app.get("/", response_class=HTMLResponse)
def serve():
    return HTML

@app.post("/audit")
def audit(req: AuditRequest):
    try:
        issues = run_audit(req.url, req.report_year, req.base_year)
        return {"status": "ok", "issues": issues}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)