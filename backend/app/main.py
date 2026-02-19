from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime

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

def parse_number_ptbr(s: str) -> Optional[Any]:
    """Converte string numérica PT-BR para número."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if re.match(r'^\d{1,3}(\.\d{3})+$', s):
        return int(s.replace('.', ''))
    if re.match(r'^\d{1,3}(\.\d{3})*,\d+$', s):
        try:
            return float(s.replace('.', '').replace(',', '.'))
        except:
            return None
    if re.match(r'^\d+\.\d+$', s):
        try:
            return float(s)
        except:
            return None
    if re.match(r'^\d+$', s):
        try:
            return int(s)
        except:
            return None
    return None

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
        return "", {"tamanho_html_kb": 0, "contagem_tables": 0, "status": f"ERRO: {str(e)}"}

def extract_tables_from_html(html: str) -> List[Dict]:
    """Extrai tabelas"""
    tables = []
    soup = BeautifulSoup(html, 'html.parser')
    for table_idx, table_elem in enumerate(soup.find_all('table'), 1):
        caption = table_elem.find('caption')
        table_name = f"Tabela {table_idx}"
        if caption:
            table_name = caption.get_text(strip=True)
        headers = []
        thead = table_elem.find('thead')
        if thead:
            for th in thead.find_all('th'):
                headers.append(th.get_text(strip=True))
        rows_raw = []
        tbody = table_elem.find('tbody')
        if tbody:
            for tr in tbody.find_all('tr'):
                cells = [td.get_text(strip=True) for td in tr.find_all('td')]
                if cells:
                    rows_raw.append(cells)
        if rows_raw:
            tables.append({
                "numero": table_idx,
                "nome": table_name,
                "headers": headers,
                "rows_raw": rows_raw,
                "html": str(table_elem)
            })
    return tables

# ===== REGRA CORRIGIDA: extreme_year_variation =====

def detect_year_columns(headers: List[str]) -> List[int]:
    """
    CORRIGIDO: Detecta colunas que são anos (20XX).
    Retorna índices das colunas que contêm anos.
    
    Exemplo:
    ["Universidade", "2022", "2023", "2024"] → [1, 2, 3]
    ["Campus", "2024", "Total"] → [1]
    """
    year_cols = []
    for col_idx, header in enumerate(headers):
        # Regex: ^20\d{2}$ detecta "2020", "2021", ..., "2029"
        if re.match(r'^20\d{2}$', header.strip()):
            year_cols.append(col_idx)
    return year_cols

def has_time_series_indicator(table_name: str) -> bool:
    """
    Verifica se título da tabela indica série temporal.
    Detecta: "série", "evolução", "histórico", "trend", "2020 a 2024", etc.
    """
    indicators = ['série', 'evolução', 'históric', 'trend', 'período', 'temporal', r'20\d{2}\s+a\s+20\d{2}']
    table_lower = table_name.lower()
    for indicator in indicators:
        if re.search(indicator, table_lower):
            return True
    return False

def rule_extreme_year_variation(table: Dict) -> Optional[Dict]:
    """
    REGRA 3 CORRIGIDA: Detecta variação extrema APENAS em séries históricas.
    
    CRITÉRIO DE DETECÇÃO DE SÉRIE TEMPORAL:
    1. Detectar colunas com headers "20XX" (anos)
    2. Se >= 3 colunas de anos → É série temporal
    3. OU se título indicar série histórica → É série temporal
    4. Senão → SKIP (retorna None)
    
    CÁLCULO CORRETO (HORIZONTAL):
    - Para cada linha (categoria),
    - Comparar valores ano N com ano N-1 (na mesma linha)
    - NUNCA comparar valores entre linhas diferentes
    
    RESULTADO:
    - FAIL se variação > 500%
    - WARN se variação 300-500%
    - None se não for série temporal (SKIP)
    """
    
    headers = table.get("headers", [])
    rows = table.get("rows_raw", [])
    table_name = table.get("nome", "Tabela desconhecida")
    
    if not headers or not rows:
        return None
    
    # ===== PASSO 1: DETECTAR SÉRIE TEMPORAL =====
    year_cols = detect_year_columns(headers)
    
    # Critério: >= 3 colunas de anos OU indicador textual
    is_time_series = len(year_cols) >= 3 or has_time_series_indicator(table_name)
    
    if not is_time_series:
        # Não é série temporal → SKIP (retorna None)
        return None
    
    # Se detectou série temporal mas < 3 anos, ainda pode processar
    if len(year_cols) < 3:
        # Pode ser tabela com 2 anos mas indicador textual
        if len(year_cols) < 2:
            # Precisa de pelo menos 2 anos para calcular variação
            return None
    
    # ===== PASSO 2: PROCESSAR CADA LINHA (CATEGORIA) =====
    
    for row_idx, row in enumerate(rows):
        # Extrair valores da linha para colunas de ano
        year_values = []
        for col_idx in year_cols:
            if col_idx < len(row):
                num = parse_number_ptbr(row[col_idx])
                if num is not None and num > 0:
                    year_header = headers[col_idx] if col_idx < len(headers) else "?"
                    year_values.append((col_idx, year_header, num))
        
        # Se < 2 valores numéricos, pular linha
        if len(year_values) < 2:
            continue
        
        # ===== PASSO 3: COMPARAR HORIZONTALMENTE (ano a ano nesta linha) =====
        for i in range(len(year_values) - 1):
            prev_col, prev_year, prev_val = year_values[i]
            curr_col, curr_year, curr_val = year_values[i + 1]
            
            # Calcular variação HORIZONTAL
            # Comparamos anos consecutivos na MESMA linha
            if prev_val > 0:
                variacao_pct = ((curr_val - prev_val) / prev_val) * 100
                
                # Extrair nome da categoria (primeiro campo)
                categoria = row[0] if row else f"Linha {row_idx + 1}"
                
                # FAIL se > 500%
                if abs(variacao_pct) > 500:
                    return {
                        "severity": "FAIL",
                        "table": table_name,
                        "rule": "extreme_year_variation",
                        "issue": f"Variação extrema > 500%",
                        "detail": f"Categoria: '{categoria}' | {prev_year}: {prev_val} → {curr_year}: {curr_val} ({variacao_pct:+.1f}%)",
                        "recommendation": "Verificar integridade dos dados"
                    }
                
                # WARN se 300-500%
                elif abs(variacao_pct) > 300:
                    return {
                        "severity": "WARN",
                        "table": table_name,
                        "rule": "extreme_year_variation",
                        "issue": f"Variação extrema 300-500%",
                        "detail": f"Categoria: '{categoria}' | {prev_year}: {prev_val} → {curr_year}: {curr_val} ({variacao_pct:+.1f}%)",
                        "recommendation": "Validar dados com fonte"
                    }
    
    # Nenhuma variação extrema detectada
    return None

def rule_table_empty(table: Dict) -> Optional[Dict]:
    """Regra 1: Tabela sem dados"""
    rows = table["rows_raw"]
    if not rows:
        return {"severity": "FAIL", "table": table["nome"], "rule": "table_empty", "issue": "Tabela vazia", "detail": "Sem linhas de dados", "recommendation": "Adicionar dados"}
    has_nonzero = any(parse_number_ptbr(cell) not in (None, 0) for row in rows for cell in row)
    if not has_nonzero:
        return {"severity": "FAIL", "table": table["nome"], "rule": "table_empty", "issue": "Todos zeros", "detail": "Sem valores numéricos não-zero", "recommendation": "Verificar dados"}
    return None

def rule_table_without_data(table: Dict) -> Optional[Dict]:
    """Regra 2: Tabela sem colunas numéricas"""
    rows = table["rows_raw"]
    if not rows:
        return None
    has_numeric = False
    for col_idx in range(len(rows[0])):
        for row in rows:
            if col_idx < len(row) and parse_number_ptbr(row[col_idx]) is not None:
                has_numeric = True
                break
        if has_numeric:
            break
    if not has_numeric:
        return {"severity": "FAIL", "table": table["nome"], "rule": "table_without_data", "issue": "Sem quantitativos", "detail": "Sem colunas numéricas", "recommendation": "Adicionar dados"}
    return None

def rule_duplicated_category_structure(table: Dict) -> Optional[Dict]:
    """Regra 6: Linhas duplicadas"""
    rows = table["rows_raw"]
    if not rows or len(rows) < 2:
        return None
    numeric_signatures = {}
    for row_idx, row in enumerate(rows):
        sig = tuple(parse_number_ptbr(cell) for cell in row)
        if sig in numeric_signatures:
            return {"severity": "WARN", "table": table["nome"], "rule": "duplicated_category_structure", "issue": "Linhas com valores iguais", "detail": f"Linhas {numeric_signatures[sig]+1} e {row_idx+1} idênticas", "recommendation": "Verificar duplicação"}
        numeric_signatures[sig] = row_idx
    return None

def analyze_table(table: Dict) -> List[Dict]:
    """Analisa tabela"""
    issues = []
    issue = rule_table_empty(table)
    if issue:
        issues.append(issue)
        return issues
    issue = rule_table_without_data(table)
    if issue:
        issues.append(issue)
        return issues
    issue = rule_extreme_year_variation(table)
    if issue:
        issues.append(issue)
    issue = rule_duplicated_category_structure(table)
    if issue:
        issues.append(issue)
    return issues

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    """Auditoria completa"""
    issues = []
    html, diag = download_page(url)
    if diag["contagem_tables"] == 0:
        issues.append({"severity": "FAIL", "table": "Documento", "rule": "document", "issue": "Nenhuma tabela", "detail": "Arquivo sem tabelas HTML", "recommendation": "Verificar URL"})
        return issues
    issues.append({"severity": "PASS", "table": "Documento", "rule": "document", "issue": f"✓ {diag['contagem_tables']} tabela(s)", "detail": f"{diag['tamanho_html_kb']:.1f} KB", "recommendation": "Analisando"})
    tables = extract_tables_from_html(html)
    for table in tables:
        issues.extend(analyze_table(table))
    return issues

def generate_txt_report(issues: List[Dict], url: str, report_year: int) -> str:
    """Gera relatório TXT"""
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    txt = "=" * 80 + "\nAUDITORIA DO ANUÁRIO ESTATÍSTICO UnB\n" + "=" * 80 + "\n\n"
    txt += f"Data: {now}\nURL: {url}\nAno: {report_year}\n\n"
    fail = [i for i in issues if i["severity"] == "FAIL"]
    warn = [i for i in issues if i["severity"] == "WARN"]
    passr = [i for i in issues if i["severity"] == "PASS"]
    txt += f"FAIL: {len(fail)} | WARN: {len(warn)} | PASS: {len(passr)}\n\n"
    if fail:
        txt += "ERROS:\n" + "-" * 40 + "\n"
        for i, issue in enumerate(fail, 1):
            txt += f"{i}. {issue['issue']} ({issue['table']})\n   {issue['detail']}\n"
    if warn:
        txt += "\nAVISOS:\n" + "-" * 40 + "\n"
        for i, issue in enumerate(warn, 1):
            txt += f"{i}. {issue['issue']} ({issue['table']})\n   {issue['detail']}\n"
    return txt

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Auditoria UnB</title>
    <style>
        body { font-family: Arial; background: linear-gradient(135deg, #003366, #2E1D86); min-height: 100vh; padding: 20px; }
        .container { background: white; border-radius: 12px; max-width: 1000px; margin: 0 auto; padding: 40px; }
        h1 { color: #003366; }
        input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 6px; }
        button { width: 100%; padding: 12px; background: #003366; color: white; border: none; border-radius: 6px; cursor: pointer; margin: 10px 0; }
        .results { display: none; margin-top: 30px; }
        .issue { padding: 15px; margin: 10px 0; border-left: 5px solid; border-radius: 4px; }
        .fail { background: #ffebee; border-color: #f44336; }
        .warn { background: #fff3e0; border-color: #ff9800; }
        .pass { background: #e8f5e9; border-color: #4caf50; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <input type="url" id="url" value="https://anuariounb2025.netlify.app/" placeholder="URL">
        <input type="number" id="year" value="2025" placeholder="Ano">
        <input type="number" id="base" value="2024" placeholder="Ano Base">
        <button onclick="audit()">🔍 Executar</button>
        <div id="results" class="results">
            <div id="content"></div>
            <button onclick="downloadReport()" style="background: #27ae60;">📥 Baixar TXT</button>
        </div>
    </div>
    <script>
        let lastIssues = [], lastUrl = '', lastYear = 2025;
        async function audit() {
            const url = document.getElementById('url').value;
            const year = parseInt(document.getElementById('year').value);
            const base = parseInt(document.getElementById('base').value);
            lastUrl = url; lastYear = year;
            try {
                const res = await fetch('https://revisor-anuario-2.onrender.com/audit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, report_year: year, base_year: base })
                });
                const data = await res.json();
                lastIssues = data.issues;
                const html = data.issues.map(i => `
                    <div class="issue ${i.severity.toLowerCase()}">
                        <strong>${i.issue}</strong><br>
                        <small>${i.table} | ${i.detail}</small><br>
                        💡 ${i.recommendation}
                    </div>
                `).join('');
                document.getElementById('content').innerHTML = html;
                document.getElementById('results').style.display = 'block';
            } catch (e) {
                alert('Erro: ' + e.message);
            }
        }
        function downloadReport() {
            fetch('https://revisor-anuario-2.onrender.com/export/txt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ issues: lastIssues, url: lastUrl, report_year: lastYear })
            })
            .then(r => r.blob())
            .then(blob => {
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `auditoria-${Date.now()}.txt`;
                a.click();
            });
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

@app.post("/export/txt")
def export_txt(data: dict):
    txt = generate_txt_report(data.get("issues", []), data.get("url", ""), data.get("report_year", 2025))
    return StreamingResponse(iter([txt.encode('utf-8')]), media_type="text/plain; charset=utf-8", 
                            headers={"Content-Disposition": f"attachment; filename=auditoria-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"})

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)