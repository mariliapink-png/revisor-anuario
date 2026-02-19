import re
from typing import List, Dict, Tuple, Optional, Any
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Auditoria Anuário UnB")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class AuditRequest(BaseModel):
    url: str
    report_year: int
    base_year: int

WEIRD_CHARS_RE = re.compile(r'[\u0000-\u001f\u007f\uFFFD\u00AD\u200B\u200E\u200F\u2028\u2029]')

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = s.replace("\xa0", " ").replace("\u00a0", " ")
    s = WEIRD_CHARS_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_number_ptbr(s: str) -> Optional[Any]:
    if not s or not isinstance(s, str):
        return None
    s = normalize_text(s)
    s = re.sub(r'[%]$', '', s).strip()
    if re.match(r'^\d{1,3}(\.\d{3})+$', s):
        return int(s.replace('.', ''))
    if re.match(r'^\d{1,3}(\.\d{3})*,\d+$', s):
        try:
            return float(s.replace('.', '').replace(',', '.'))
        except:
            return None
    if re.match(r'^\d+,\d+$', s):
        try:
            return float(s.replace(',', '.'))
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    try:
        resp = requests.get(url, timeout=30, headers=headers)
        resp.encoding = "utf-8"
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        return html, {"tamanho_html_kb": len(html) / 1024, "contagem_tables": len(tables), "status": "OK"}
    except Exception as e:
        return "", {"tamanho_html_kb": 0, "contagem_tables": 0, "status": f"ERRO: {str(e)}"}

def extract_tables_from_html(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    all_tables = soup.find_all("table")
    for table_idx, table_elem in enumerate(all_tables, 1):
        caption = table_elem.find("caption")
        table_name = normalize_text(caption.get_text(" ", strip=True)) if caption else f"Tabela {table_idx}"
        headers = []
        thead = table_elem.find("thead")
        if thead:
            headers = [normalize_text(th.get_text(" ", strip=True)) for th in thead.find_all("th")]
        rows_raw = []
        tbody = table_elem.find("tbody") or table_elem
        for tr in tbody.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
            if any(c != "" for c in cells):
                rows_raw.append(cells)
        tables.append({"numero": table_idx, "nome": table_name, "headers": headers, "rows_raw": rows_raw, "html": str(table_elem)})
    return tables

# ============================================================
# REGRAS ESPECÍFICAS PARA ERROS DO CAPÍTULO 2
# ============================================================

def rule_missing_digit_in_number(table: Dict) -> Optional[Dict]:
    """Detecta erro de digitação: número que parece estar faltando dígito (ex: 1031 vs 10031)"""
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    
    for r_i, row in enumerate(rows):
        for c_i, cell in enumerate(row):
            num = parse_number_ptbr(cell)
            if num is None or not isinstance(num, int) or num < 100:
                continue
            
            # Procurar se 10x deste número existe na mesma tabela
            for other_row in rows:
                for other_cell in other_row:
                    other_num = parse_number_ptbr(other_cell)
                    if other_num and isinstance(other_num, int):
                        # Se um número é exatamente 10x o outro, suspeita de dígito faltante
                        if other_num == num * 10 and 1000 <= num <= 2000:
                            return {
                                "severity": "FAIL",
                                "table": table["nome"],
                                "rule": "missing_digit",
                                "issue": "Possível erro de digitação (dígito faltante)",
                                "detail": f"Valor '{num}' pode ser '{int(num*10)}' (10x maior aparece na tabela)",
                                "recommendation": "Verificar se número não tem dígito faltante."
                            }
    return None

def rule_identical_values_different_periods(table: Dict) -> Optional[Dict]:
    """Detecta valores idênticos em períodos/anos diferentes (suspeitamente igual)"""
    rows = table.get("rows_raw", [])
    headers = [normalize_text(h) for h in table.get("headers", [])]
    
    if len(rows) < 2:
        return None
    
    # Procurar por colunas de anos/períodos
    year_cols = []
    for col_idx, header in enumerate(headers):
        if re.match(r'^20\d{2}$', header) or 'ano' in header.lower():
            year_cols.append((col_idx, header))
    
    if len(year_cols) < 2:
        return None
    
    for row_idx, row in enumerate(rows):
        values_by_period = []
        for col_idx, period in year_cols:
            if col_idx < len(row):
                v = parse_number_ptbr(row[col_idx])
                if v is not None:
                    values_by_period.append((period, v, col_idx))
        
        # Se valores são idênticos em períodos diferentes
        if len(values_by_period) >= 2:
            for i in range(len(values_by_period) - 1):
                if values_by_period[i][1] == values_by_period[i+1][1]:
                    return {
                        "severity": "WARN",
                        "table": table["nome"],
                        "rule": "duplicate_period_values",
                        "issue": "Valores idênticos em períodos diferentes",
                        "detail": f"'{row[0]}': {values_by_period[i][0]}={values_by_period[i][1]:.0f} = {values_by_period[i+1][0]}={values_by_period[i+1][1]:.0f}",
                        "recommendation": "Verificar se dados foram copiados ou realmente são iguais."
                    }
    
    return None

def rule_missing_field_standardized_table(table: Dict) -> Optional[Dict]:
    """Detecta campo ausente em tabela estruturada (ex: Campus Ceilândia sem Área Total)"""
    rows = table.get("rows_raw", [])
    
    if len(rows) < 3:
        return None
    
    col_counts = [len(row) for row in rows]
    max_cols = max(col_counts)
    
    # Procurar linha com coluna faltante
    for row_idx, row in enumerate(rows):
        if len(row) < max_cols and len(row) > 1:
            # Verificar se outras linhas têm dados naquele índice
            has_data = any(parse_number_ptbr(row[i]) is not None for i in range(len(row)))
            if has_data:
                return {
                    "severity": "WARN",
                    "table": table["nome"],
                    "rule": "missing_field",
                    "issue": f"Campo ausente em linha estruturada (linha {row_idx+1})",
                    "detail": f"'{row[0]}' tem {len(row)} colunas, esperado {max_cols}",
                    "recommendation": "Preenc her campo faltante ou verificar formatação."
                }
    
    return None

def rule_disproportionate_distribution(table: Dict) -> Optional[Dict]:
    """Detecta distribuição muito desproporcional entre colunas (ex: Enem 4 vs 2205)"""
    rows = table.get("rows_raw", [])
    headers = [normalize_text(h) for h in table.get("headers", [])]
    
    if len(rows) < 2:
        return None
    
    # Procurar por padrão "1º Sem" e "2º Sem"
    col_1sem = None
    col_2sem = None
    for idx, h in enumerate(headers):
        if '1º' in h or 'i semestre' in h.lower():
            col_1sem = idx
        if '2º' in h or 'ii semestre' in h.lower():
            col_2sem = idx
    
    if col_1sem is None or col_2sem is None:
        return None
    
    for row in rows:
        if col_1sem < len(row) and col_2sem < len(row):
            v1 = parse_number_ptbr(row[col_1sem])
            v2 = parse_number_ptbr(row[col_2sem])
            
            if v1 and v2 and v1 > 0 and v2 > 0:
                # Se proporção muito desproporcional (>100:1)
                if v2 > v1 * 100 or v1 > v2 * 100:
                    ratio = max(v1, v2) / min(v1, v2)
                    return {
                        "severity": "WARN",
                        "table": table["nome"],
                        "rule": "disproportionate_distribution",
                        "issue": "Distribuição muito desproporcional por período",
                        "detail": f"'{row[0]}': 1º={v1:g}, 2º={v2:g} (proporção 1:{ratio:.0f})",
                        "recommendation": "Verificar se distribuição por semestre está correta."
                    }
    
    return None

def rule_abrupt_drop_series(table: Dict) -> Optional[Dict]:
    """Detecta queda abrupta >50% em série de anos"""
    rows = table.get("rows_raw", [])
    headers = [normalize_text(h) for h in table.get("headers", [])]
    
    if len(rows) < 2:
        return None
    
    year_cols = []
    for col_idx, header in enumerate(headers):
        if re.match(r'^20\d{2}$', header):
            year_cols.append((col_idx, header))
    
    if len(year_cols) < 2:
        return None
    
    for row in rows:
        vals = []
        for col_idx, year in year_cols:
            if col_idx < len(row):
                v = parse_number_ptbr(row[col_idx])
                if v is not None and v > 0:
                    vals.append((year, float(v)))
        
        if len(vals) >= 2:
            for i in range(len(vals) - 1):
                y0, v0 = vals[i]
                y1, v1 = vals[i + 1]
                
                if v0 > 0:
                    pct_change = ((v1 - v0) / v0) * 100
                    # Queda >50% (ex: 52→4)
                    if pct_change < -50:
                        return {
                            "severity": "WARN",
                            "table": table["nome"],
                            "rule": "abrupt_drop",
                            "issue": f"Queda abrupta (>{abs(pct_change):.0f}%)",
                            "detail": f"'{row[0]}': {y0}={v0:g} → {y1}={v1:g} ({pct_change:+.1f}%)",
                            "recommendation": "Validar se é erro ou mudança real de critério/política."
                        }
    
    return None

def rule_sum_total_mismatch(table: Dict) -> Optional[Dict]:
    """Detecta discrepância entre soma de linhas e total"""
    rows = table.get("rows_raw", [])
    
    if len(rows) < 3:
        return None
    
    # Procurar linha Total
    total_idx = None
    for i, row in enumerate(rows):
        if row and re.match(r'^\s*total\b', normalize_text(row[0]), flags=re.IGNORECASE):
            total_idx = i
            break
    
    if total_idx is None or total_idx < 2:
        return None
    
    # Verificar coluna numérica
    for col_idx in range(1, min(6, len(rows[0]) if rows else 0)):
        soma = 0.0
        has_vals = False
        
        for r in rows[:total_idx]:
            if col_idx < len(r):
                v = parse_number_ptbr(r[col_idx])
                if v is not None:
                    soma += float(v)
                    has_vals = True
        
        if not has_vals:
            continue
        
        total_cell = rows[total_idx][col_idx] if col_idx < len(rows[total_idx]) else ""
        total_val = parse_number_ptbr(total_cell)
        
        if total_val is None:
            continue
        
        # Se soma é significativamente diferente do total
        diff = abs(soma - float(total_val))
        if diff > max(10, abs(soma) * 0.05):
            return {
                "severity": "FAIL",
                "table": table["nome"],
                "rule": "sum_total_mismatch",
                "issue": "Soma das linhas ≠ total declarado",
                "detail": f"Coluna {col_idx+1}: soma={soma:g} vs total={float(total_val):g}",
                "recommendation": "Recalcular total ou revisar linhas incluídas."
            }
    
    return None

def rule_blank_cells(table: Dict) -> Optional[Dict]:
    """Detecta células em branco"""
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    
    blanks = []
    for r_i, row in enumerate(rows, 1):
        for c_i, cell in enumerate(row, 1):
            if normalize_text(cell) == "":
                blanks.append((r_i, c_i))
                if len(blanks) >= 8:
                    break
        if len(blanks) >= 8:
            break
    
    if blanks and len(blanks) > 3:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "blank_cells",
            "issue": "Múltiplas células em branco",
            "detail": f"Exemplos: {', '.join([f'(L{r},C{c})' for r,c in blanks[:6]])}",
            "recommendation": "Preencher ou padronizar com zeros explícitos."
        }
    return None

def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues = []
    
    for rule in (
        rule_missing_digit_in_number,
        rule_identical_values_different_periods,
        rule_missing_field_standardized_table,
        rule_disproportionate_distribution,
        rule_abrupt_drop_series,
        rule_sum_total_mismatch,
        rule_blank_cells,
    ):
        out = rule(table)
        if out:
            issues.append(out)
    
    return issues

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    issues = []
    html, diag = download_page(url)
    
    if diag.get("contagem_tables", 0) == 0:
        issues.append({
            "severity": "FAIL",
            "table": "Documento",
            "rule": "no_tables",
            "issue": "Nenhuma tabela encontrada",
            "detail": f"Status: {diag.get('status')}",
            "recommendation": "Verificar URL."
        })
        return issues
    
    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "rule": "scan_ok",
        "issue": f"✓ {diag['contagem_tables']} tabela(s)",
        "detail": f"HTML: {diag['tamanho_html_kb']:.1f} KB",
        "recommendation": "Analisando..."
    })
    
    tables = extract_tables_from_html(html)
    for table in tables:
        issues.extend(analyze_table(table, base_year))
    
    return issues

def generate_txt_report(issues: List[Dict], url: str, report_year: int, base_year: int) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    txt = "=" * 80 + "\nAUDITORIA DO ANUÁRIO ESTATÍSTICO UnB\n" + "=" * 80 + "\n\n"
    txt += f"Data: {now}\nURL: {url}\nAno: {report_year}\nAno-base: {base_year}\n\n"
    
    fail = [i for i in issues if i["severity"] == "FAIL"]
    warn = [i for i in issues if i["severity"] == "WARN"]
    passed = [i for i in issues if i["severity"] == "PASS"]
    
    txt += f"FAIL: {len(fail)} | WARN: {len(warn)} | PASS: {len(passed)}\n\n"
    
    if fail:
        txt += "ERROS (FAIL):\n" + "-" * 60 + "\n"
        for i, issue in enumerate(fail, 1):
            txt += f"{i}. {issue['issue']} ({issue['table']})\n   {issue['detail']}\n   💡 {issue['recommendation']}\n\n"
    
    if warn:
        txt += "AVISOS (WARN):\n" + "-" * 60 + "\n"
        for i, issue in enumerate(warn, 1):
            txt += f"{i}. {issue['issue']} ({issue['table']})\n   {issue['detail']}\n   💡 {issue['recommendation']}\n\n"
    
    return txt

HTML_FRONTEND = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria - Anuário UnB</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #003366 0%, #2E1D86 100%); min-height: 100vh; padding: 20px; }
        .container { background: white; border-radius: 12px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); max-width: 1000px; margin: 0 auto; padding: 40px; }
        h1 { color: #003366; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #003366; font-weight: 600; margin-bottom: 8px; }
        input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        button { width: 100%; padding: 12px; background: linear-gradient(135deg, #003366 0%, #2E1D86 100%); color: white; border: none; border-radius: 6px; font-weight: 600; cursor: pointer; margin-top: 20px; }
        button:hover { opacity: 0.9; }
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
        .rule-tag { display: inline-block; padding: 2px 8px; background: #f0f0f0; border-radius: 3px; font-size: 11px; margin-top: 8px; }
        .export-button { background: #27ae60; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <p class="subtitle">Detecção de erros de digitação, dados faltantes e inconsistências</p>
        <div id="form">
            <div class="form-group">
                <label>URL do Anuário</label>
                <input type="url" id="url" value="https://anuario2024.netlify.app/">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Ano do Anuário</label>
                    <input type="number" id="year" value="2024">
                </div>
                <div class="form-group">
                    <label>Ano-base</label>
                    <input type="number" id="baseYear" value="2023">
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
            <h2 style="color: #003366; margin-bottom: 20px;">Resultados:</h2>
            <div id="content"></div>
            <button class="export-button" onclick="downloadReport()">📥 Baixar Relatório (TXT)</button>
        </div>
    </div>
    <script>
        let lastIssues = [], lastUrl = '', lastYear = 2024, lastBase = 2023;
        async function audit() {
            const url = document.getElementById('url').value;
            const year = parseInt(document.getElementById('year').value);
            const base = parseInt(document.getElementById('baseYear').value);
            lastUrl = url; lastYear = year; lastBase = base;
            document.getElementById('form').style.display = 'none';
            document.getElementById('loading').style.display = 'block';
            try {
                const res = await fetch('/audit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, report_year: year, base_year: base })
                });
                const data = await res.json();
                lastIssues = data.issues;
                showResults(data.issues);
            } catch (e) {
                alert('Erro: ' + e.message);
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
            }
        }
        function showResults(issues) {
            const pass = issues.filter(i => i.severity === 'PASS').length;
            const warn = issues.filter(i => i.severity === 'WARN').length;
            const fail = issues.filter(i => i.severity === 'FAIL').length;
            document.getElementById('stats').innerHTML = `
                <div class="stat"><div class="stat-number" style="color: #4caf50;">${pass}</div><div>OK</div></div>
                <div class="stat"><div class="stat-number" style="color: #ff9800;">${warn}</div><div>Avisos</div></div>
                <div class="stat"><div class="stat-number" style="color: #f44336;">${fail}</div><div>Erros</div></div>
            `;
            document.getElementById('content').innerHTML = issues.map(i => `
                <div class="issue-item ${i.severity.toLowerCase()}">
                    <div style="display: flex; justify-content: space-between;">
                        <strong>${i.table}</strong>
                        <span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span>
                    </div>
                    <div style="color: #333; font-weight: 500; margin: 8px 0;">${i.issue}</div>
                    <div style="color: #555; font-size: 14px; margin: 8px 0;">${i.detail}</div>
                    <div style="color: #666; font-size: 13px; padding: 10px; background: rgba(0,0,0,0.03); border-radius: 4px;">💡 ${i.recommendation}</div>
                    <div class="rule-tag">Regra: ${i.rule}</div>
                </div>
            `).join('');
            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }
        function downloadReport() {
            fetch('/export/txt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ issues: lastIssues, url: lastUrl, report_year: lastYear, base_year: lastBase })
            })
            .then(r => r.blob())
            .then(blob => {
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `auditoria-${Date.now()}.txt`;
                document.body.appendChild(a);
                a.click();
                a.remove();
            });
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML_FRONTEND

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/audit")
def audit(req: AuditRequest):
    try:
        issues = run_audit(req.url, req.report_year, req.base_year)
        return {"status": "ok", "issues": issues}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/export/txt")
def export_txt(data: dict):
    txt = generate_txt_report(
        issues=data.get("issues", []),
        url=data.get("url", ""),
        report_year=int(data.get("report_year", 2024)),
        base_year=int(data.get("base_year", 2023)),
    )
    return StreamingResponse(
        iter([txt.encode("utf-8")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=auditoria-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"},
    )
