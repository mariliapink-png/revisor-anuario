from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Tuple, Optional, Any

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

# ===== PARSER NUMÉRICO ROBUSTO (PT-BR) =====

def parse_number_ptbr(s: str) -> Optional[Any]:
    """
    Converte string numérica PT-BR para número (int ou float).
    Ordem de prioridade:
    1. Padrão milhar brasileiro: "8.415", "1.769.277" => int
    2. Decimal brasileiro: "10.490,50" => float
    3. Decimal ponto sem milhar: "8.5" => float
    4. Nenhum => None
    """
    if not s or not isinstance(s, str):
        return None
    
    s = s.strip()
    
    # 1. Padrão milhar: ^\d{1,3}(\.\d{3})+$
    if re.match(r'^\d{1,3}(\.\d{3})+$', s):
        return int(s.replace('.', ''))
    
    # 2. Padrão decimal brasileiro: ^\d{1,3}(\.\d{3})*,\d+$
    if re.match(r'^\d{1,3}(\.\d{3})*,\d+$', s):
        try:
            return float(s.replace('.', '').replace(',', '.'))
        except:
            return None
    
    # 3. Padrão decimal ponto: ^\d+\.\d+$
    if re.match(r'^\d+\.\d+$', s):
        try:
            return float(s)
        except:
            return None
    
    # 4. Número inteiro: ^\d+$
    if re.match(r'^\d+$', s):
        try:
            return int(s)
        except:
            return None
    
    return None

# ===== DOWNLOAD =====

def download_page(url: str) -> Tuple[str, Dict]:
    """Baixa HTML com headers realistas"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9',
    }
    
    try:
        resp = requests.get(url, timeout=20, headers=headers)
        resp.encoding = 'utf-8'
        html = resp.text
        
        soup = BeautifulSoup(html, 'html.parser')
        tables = soup.find_all('table')
        
        return html, {
            "tamanho_html_kb": len(html) / 1024,
            "contagem_tables": len(tables),
            "status": "OK"
        }
    except Exception as e:
        return "", {
            "tamanho_html_kb": 0,
            "contagem_tables": 0,
            "status": f"ERRO: {str(e)}"
        }

# ===== EXTRAÇÃO DE TABELAS =====

def extract_tables_from_html(html: str) -> List[Dict]:
    """Extrai tabelas com busca ampliada de Fonte"""
    tables = []
    soup = BeautifulSoup(html, 'html.parser')
    
    for table_idx, table_elem in enumerate(soup.find_all('table'), 1):
        caption = table_elem.find('caption')
        table_name = f"Tabela {table_idx}"
        if caption:
            table_name = caption.get_text(strip=True)
        
        # Headers
        headers = []
        thead = table_elem.find('thead')
        if thead:
            for th in thead.find_all('th'):
                headers.append(th.get_text(strip=True))
        
        # Dados brutos
        rows_raw = []
        tbody = table_elem.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all('td')]
                if cells:
                    rows_raw.append(cells)
        
        # Buscar Fonte em raio ampliado
        fonte = find_fonte_ampliado(table_elem, soup)
        
        if rows_raw:
            tables.append({
                "numero": table_idx,
                "nome": table_name,
                "headers": headers,
                "rows_raw": rows_raw,
                "fonte": fonte,
                "html": str(table_elem)
            })
    
    return tables

def find_fonte_ampliado(table_elem, soup) -> str:
    """Procura por 'Fonte' em raio ampliado (5 antes, 10 depois) + classes comuns"""
    
    # 1. tfoot
    tfoot = table_elem.find('tfoot')
    if tfoot:
        tfoot_text = tfoot.get_text(strip=True)
        if re.search(r'[Ff]onte', tfoot_text):
            return tfoot_text[:200]
    
    # 2. 5 elementos anteriores
    prev_elem = table_elem.find_previous()
    for _ in range(5):
        if prev_elem:
            text = prev_elem.get_text(strip=True)
            if re.search(r'[Ff]onte', text):
                return text[:200]
            prev_elem = prev_elem.find_previous()
    
    # 3. 10 elementos posteriores
    next_elem = table_elem.find_next()
    for _ in range(10):
        if next_elem:
            text = next_elem.get_text(strip=True)
            if re.search(r'[Ff]onte', text):
                return text[:200]
            if next_elem.name in ['h1', 'h2', 'h3', 'table']:
                break
            next_elem = next_elem.find_next()
    
    # 4. Classes comuns
    for class_name in ['source', 'caption', 'note', 'fonte']:
        elem = soup.find(class_=class_name)
        if elem:
            text = elem.get_text(strip=True)
            if 'Fonte' in text or 'fonte' in text:
                return text[:200]
    
    # 5. figcaption
    figcaption = soup.find('figcaption')
    if figcaption:
        text = figcaption.get_text(strip=True)
        if 'Fonte' in text:
            return text[:200]
    
    return ""

# ===== ANÁLISE DE TABELAS =====

def analyze_table(table: Dict) -> List[Dict]:
    """Analisa tabela com parser numérico correto"""
    
    issues = []
    table_name = table["nome"]
    rows_raw = table["rows_raw"]
    
    if not rows_raw:
        return issues
    
    # 1. Fonte
    if not table["fonte"]:
        issues.append({
            "severity": "FAIL",
            "table": table_name,
            "issue": "Fonte não identificada",
            "detail": "Procurado em tfoot, 5 elementos antes, 10 depois, classes comuns",
            "recommendation": "Adicionar 'Fonte: [origem]'"
        })
    
    # 2. Células vazias
    total_cells = sum(len(row) for row in rows_raw)
    empty_cells = sum(1 for row in rows_raw for cell in row if not cell.strip())
    
    if total_cells > 0 and (empty_cells / total_cells * 100) > 15:
        issues.append({
            "severity": "WARN",
            "table": table_name,
            "issue": f"{empty_cells/total_cells*100:.1f}% células vazias",
            "detail": f"{empty_cells}/{total_cells}",
            "recommendation": "Padronizar (0, ND, N/A)"
        })
    
    # 3. Colunas inconsistentes
    col_counts = [len(row) for row in rows_raw]
    if len(set(col_counts)) > 1:
        issues.append({
            "severity": "FAIL",
            "table": table_name,
            "issue": f"Colunas inconsistentes: {min(col_counts)}-{max(col_counts)}",
            "detail": str(col_counts),
            "recommendation": "Verificar alinhamento"
        })
    
    # 4. Soma (APENAS com Total explícito)
    total_issue = check_soma(table_name, table["headers"], rows_raw)
    if total_issue:
        issues.append(total_issue)
    
    # 5. Formatação decimal
    all_text = ' '.join([' '.join(row) for row in rows_raw])
    comma_nums = len(re.findall(r'\d{1,3},\d{2,}', all_text))
    dot_nums = len(re.findall(r'\d+\.\d+', all_text))
    
    if comma_nums > 0 and dot_nums > 5:
        issues.append({
            "severity": "WARN",
            "table": table_name,
            "issue": "Mistura de separadores",
            "detail": f"{comma_nums} com vírgula, {dot_nums} com ponto",
            "recommendation": "Padronizar"
        })
    
    return issues

def check_soma(table_name: str, headers: list, rows_raw: list) -> Optional[Dict]:
    """Soma APENAS se houver 'Total' explícito. Usa parse_number_ptbr."""
    
    # Detectar coluna Total
    total_col_idx = None
    if headers:
        for idx, h in enumerate(headers):
            if 'Total' in h or 'total' in h.lower():
                total_col_idx = idx
                break
    
    # Detectar linha Total
    total_row_idx = None
    for idx, row in enumerate(rows_raw):
        if row and ('Total' in row[0] or 'total' in row[0].lower()):
            total_row_idx = idx
            break
    
    # Sem Total explícito = SKIP (não retornar erro)
    if total_col_idx is None and total_row_idx is None:
        return None
    
    # Soma por coluna (se houver coluna Total)
    if total_col_idx is not None:
        for row_idx, row in enumerate(rows_raw):
            if len(row) <= total_col_idx:
                continue
            
            total_str = row[total_col_idx]
            total_val = parse_number_ptbr(total_str)
            
            # Somar parciais
            parciais = []
            parciais_str = []
            for col_idx in range(len(row)):
                if col_idx != total_col_idx:
                    num = parse_number_ptbr(row[col_idx])
                    if num is not None:
                        parciais.append(num)
                        parciais_str.append(f"{row[col_idx]}({num})")
            
            if parciais and total_val is not None:
                soma = sum(parciais)
                tolerancia = 0.01 if isinstance(soma, float) or isinstance(total_val, float) else 0
                
                if abs(soma - total_val) > tolerancia:
                    return {
                        "severity": "FAIL",
                        "table": table_name,
                        "issue": f"Erro na soma (linha {row_idx+1})",
                        "detail": f"Soma: {soma} | Total: {total_val} | Parciais: {', '.join(parciais_str[:3])}",
                        "recommendation": "Recalcular o total"
                    }
    
    # Soma por linha (se houver linha Total)
    if total_row_idx is not None and total_col_idx is None:
        total_row = rows_raw[total_row_idx]
        
        for col_idx in range(1, len(total_row)):
            total_str = total_row[col_idx]
            total_val = parse_number_ptbr(total_str)
            
            if total_val is None:
                continue
            
            parciais = []
            parciais_str = []
            for row_idx in range(len(rows_raw)):
                if row_idx == total_row_idx or col_idx >= len(rows_raw[row_idx]):
                    continue
                
                num = parse_number_ptbr(rows_raw[row_idx][col_idx])
                if num is not None:
                    parciais.append(num)
                    parciais_str.append(f"{rows_raw[row_idx][col_idx]}({num})")
            
            if parciais:
                soma = sum(parciais)
                tolerancia = 0.01 if isinstance(soma, float) or isinstance(total_val, float) else 0
                
                if abs(soma - total_val) > tolerancia:
                    return {
                        "severity": "FAIL",
                        "table": table_name,
                        "issue": f"Erro na soma (coluna {col_idx})",
                        "detail": f"Soma: {soma} | Total: {total_val}",
                        "recommendation": "Recalcular o total"
                    }
    
    return None

# ===== CHECKS GLOBAIS =====

def check_year(html: str, report_year: int) -> List[Dict]:
    """Verifica anos"""
    issues = []
    year_str = str(report_year)
    
    if year_str not in html:
        issues.append({
            "severity": "FAIL",
            "table": "Metadados",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "Não aparece em captions/títulos",
            "recommendation": f"Adicionar '{year_str}'"
        })
    
    return issues

# ===== MAIN =====

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    """Auditoria completa"""
    issues = []
    
    html, diag = download_page(url)
    
    if diag["contagem_tables"] == 0:
        issues.append({
            "severity": "FAIL",
            "table": "Documento",
            "issue": "Nenhuma tabela encontrada",
            "detail": f"{diag['tamanho_html_kb']:.1f} KB",
            "recommendation": "Verificar renderização"
        })
        return issues
    
    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "issue": f"✓ {diag['contagem_tables']} tabela(s)",
        "detail": f"{diag['tamanho_html_kb']:.1f} KB",
        "recommendation": "Analisando..."
    })
    
    tables = extract_tables_from_html(html)
    
    for table in tables:
        issues.extend(analyze_table(table))
    
    issues.extend(check_year(html, report_year))
    
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
        .container { background: white; border-radius: 12px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); max-width: 1000px; margin: 0 auto; padding: 40px; }
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
            <p>Auditando...</p>
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
                <div class="stat"><div class="stat-number" style="color: #4caf50;">${pass}</div><div class="stat-label">OK</div></div>
                <div class="stat"><div class="stat-number" style="color: #ff9800;">${warn}</div><div class="stat-label">Avisos</div></div>
                <div class="stat"><div class="stat-number" style="color: #f44336;">${fail}</div><div class="stat-label">Erros</div></div>
            `;

            document.getElementById('content').innerHTML = issues.map(i => `
                <div class="issue-item ${i.severity.toLowerCase()}">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <strong>${i.table}</strong>
                        <span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span>
                    </div>
                    <div style="color: #333; font-weight: 500; margin-bottom: 8px;">${i.issue}</div>
                    <div style="color: #555; font-size: 14px; margin-bottom: 10px;">${i.detail}</div>
                    <div style="color: #666; font-style: italic; font-size: 13px; padding: 10px; background: rgba(0,0,0,0.03); border-radius: 4px;">💡 ${i.recommendation}</div>
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