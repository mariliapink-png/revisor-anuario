from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime
import io

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
    """Converte string numérica PT-BR para número (int ou float)."""
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
    """Extrai tabelas com contexto ampliado"""
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
    """Procura por Fonte em raio ampliado"""
    tfoot = table_elem.find('tfoot')
    if tfoot:
        tfoot_text = tfoot.get_text(strip=True)
        if re.search(r'[Ff]onte', tfoot_text):
            return tfoot_text[:200]
    
    prev_elem = table_elem.find_previous()
    for _ in range(5):
        if prev_elem:
            text = prev_elem.get_text(strip=True)
            if re.search(r'[Ff]onte', text):
                return text[:200]
            prev_elem = prev_elem.find_previous()
    
    next_elem = table_elem.find_next()
    for _ in range(10):
        if next_elem:
            text = next_elem.get_text(strip=True)
            if re.search(r'[Ff]onte', text):
                return text[:200]
            if next_elem.name in ['h1', 'h2', 'h3', 'table']:
                break
            next_elem = next_elem.find_next()
    
    for class_name in ['source', 'caption', 'note', 'fonte']:
        elem = soup.find(class_=class_name)
        if elem:
            text = elem.get_text(strip=True)
            if 'Fonte' in text:
                return text[:200]
    
    figcaption = soup.find('figcaption')
    if figcaption:
        text = figcaption.get_text(strip=True)
        if 'Fonte' in text:
            return text[:200]
    
    return ""

# ===== NOVAS REGRAS DO CAPÍTULO 9 =====

def rule_table_empty(table: Dict) -> Optional[Dict]:
    """Regra 1: Tabela sem dados"""
    rows = table["rows_raw"]
    
    if not rows:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "table_empty",
            "issue": "Tabela estruturada sem dados preenchidos",
            "detail": "DataFrame vazio ou apenas cabeçalhos",
            "recommendation": "Preencher dados na tabela"
        }
    
    has_nonzero = False
    for row in rows:
        for cell in row:
            num = parse_number_ptbr(cell)
            if num is not None and num != 0:
                has_nonzero = True
                break
        if has_nonzero:
            break
    
    if not has_nonzero:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "table_empty",
            "issue": "Tabela com apenas zeros ou valores nulos",
            "detail": "Todas as células numéricas contêm zero ou None",
            "recommendation": "Verificar se dados estão faltando"
        }
    
    return None

def rule_table_without_data(table: Dict) -> Optional[Dict]:
    """Regra 2: Tabela sem colunas numéricas"""
    rows = table["rows_raw"]
    
    if not rows:
        return None
    
    has_numeric_column = False
    for col_idx in range(len(rows[0])):
        for row in rows:
            if col_idx < len(row):
                num = parse_number_ptbr(row[col_idx])
                if num is not None:
                    has_numeric_column = True
                    break
        if has_numeric_column:
            break
    
    if not has_numeric_column:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "table_without_data",
            "issue": "Tabela sem quantitativos",
            "detail": "Linhas de categorias encontradas, mas nenhuma coluna numérica válida",
            "recommendation": "Adicionar dados numéricos ou remover tabela vazia"
        }
    
    return None

def rule_extreme_year_variation(table: Dict) -> Optional[Dict]:
    """Regra 4: Variação extrema ano-a-ano"""
    rows = table["rows_raw"]
    
    if not rows or len(rows) < 3:
        return None
    
    for col_idx in range(1, len(rows[0])):
        values = []
        for row_idx, row in enumerate(rows):
            if col_idx < len(row):
                num = parse_number_ptbr(row[col_idx])
                if num is not None and num != 0:
                    values.append((row_idx, num))
        
        if len(values) < 3:
            continue
        
        for i in range(len(values) - 1):
            prev_val = values[i][1]
            curr_val = values[i + 1][1]
            
            if prev_val > 0:
                variacao_pct = ((curr_val - prev_val) / prev_val) * 100
                
                if abs(variacao_pct) > 500:
                    return {
                        "severity": "FAIL",
                        "table": table["nome"],
                        "rule": "extreme_year_variation",
                        "issue": f"Variação extrema > 500% (col {col_idx})",
                        "detail": f"Linha {values[i][0]+1}: {prev_val} → Linha {values[i+1][0]+1}: {curr_val} ({variacao_pct:.1f}%)",
                        "recommendation": "Verificar integridade dos dados"
                    }
                elif abs(variacao_pct) > 300:
                    return {
                        "severity": "WARN",
                        "table": table["nome"],
                        "rule": "extreme_year_variation",
                        "issue": f"Variação extrema 300-500% (col {col_idx})",
                        "detail": f"Linha {values[i][0]+1}: {prev_val} → Linha {values[i+1][0]+1}: {curr_val} ({variacao_pct:.1f}%)",
                        "recommendation": "Validar dados com fonte"
                    }
    
    return None

def rule_duplicated_category_structure(table: Dict) -> Optional[Dict]:
    """Regra 6: Linhas com valores idênticos"""
    rows = table["rows_raw"]
    
    if not rows or len(rows) < 2:
        return None
    
    numeric_signatures = {}
    for row_idx, row in enumerate(rows):
        sig = tuple(parse_number_ptbr(cell) for cell in row)
        
        if sig in numeric_signatures:
            prev_row_idx = numeric_signatures[sig]
            return {
                "severity": "WARN",
                "table": table["nome"],
                "rule": "duplicated_category_structure",
                "issue": "Linhas com valores numéricos idênticos",
                "detail": f"Linha {prev_row_idx+1} ({row[0]}) e Linha {row_idx+1} ({row[0]}) têm mesmos valores",
                "recommendation": "Verificar se é duplicação ou categorias distintas"
            }
        
        numeric_signatures[sig] = row_idx
    
    return None

def rule_thousand_separator_inconsistency(html: str) -> Optional[Dict]:
    """Regra 8: Mistura de separadores"""
    has_no_sep = bool(re.search(r'\b\d{4,}(?!\.\d)', html))
    has_dot_sep = bool(re.search(r'\d{1,3}\.\d{3}', html))
    
    if has_no_sep and has_dot_sep:
        return {
            "severity": "WARN",
            "table": "Documento",
            "rule": "thousand_separator_inconsistency",
            "issue": "Inconsistência de separador de milhar",
            "detail": "Encontrados números como '1290' e '1.290' simultaneamente",
            "recommendation": "Padronizar separador (usar 1.290 ou 1290 consistentemente)"
        }
    
    return None

def rule_spelling_error_detection(html: str) -> Optional[Dict]:
    """Regra 9: Erros de ortografia"""
    if "Regulamente" in html or "regulamente" in html:
        return {
            "severity": "WARN",
            "table": "Documento",
            "rule": "spelling_error_detection",
            "issue": "Erro de ortografia detectado",
            "detail": "Encontrado 'Regulamente' (correto: 'Regularmente')",
            "recommendation": "Corrigir ortografia"
        }
    
    return None

# ===== ANÁLISE E EXPORT =====

def analyze_table(table: Dict, html: str, base_year: int) -> List[Dict]:
    """Analisa tabela com todas as regras"""
    
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

def analyze_document(html: str, base_year: int) -> List[Dict]:
    """Análises a nível de documento"""
    
    issues = []
    
    issue = rule_thousand_separator_inconsistency(html)
    if issue:
        issues.append(issue)
    
    issue = rule_spelling_error_detection(html)
    if issue:
        issues.append(issue)
    
    return issues

def check_year(html: str, report_year: int) -> List[Dict]:
    """Verifica anos"""
    issues = []
    year_str = str(report_year)
    
    if year_str not in html:
        issues.append({
            "severity": "FAIL",
            "table": "Metadados",
            "rule": "year_check",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "Não aparece em captions/títulos",
            "recommendation": f"Adicionar '{year_str}'"
        })
    
    return issues

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    """Auditoria completa"""
    issues = []
    
    html, diag = download_page(url)
    
    if diag["contagem_tables"] == 0:
        issues.append({
            "severity": "FAIL",
            "table": "Documento",
            "rule": "document_structure",
            "issue": "Nenhuma tabela encontrada",
            "detail": f"{diag['tamanho_html_kb']:.1f} KB",
            "recommendation": "Verificar renderização"
        })
        return issues
    
    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "rule": "document_structure",
        "issue": f"✓ {diag['contagem_tables']} tabela(s)",
        "detail": f"{diag['tamanho_html_kb']:.1f} KB",
        "recommendation": "Analisando..."
    })
    
    issues.extend(analyze_document(html, base_year))
    
    tables = extract_tables_from_html(html)
    
    for table in tables:
        issues.extend(analyze_table(table, html, base_year))
    
    issues.extend(check_year(html, report_year))
    
    return issues

def generate_txt_report(issues: List[Dict], url: str, report_year: int) -> str:
    """Gera relatório TXT formatado"""
    
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    txt = "=" * 80 + "\n"
    txt += "AUDITORIA DO ANUÁRIO ESTATÍSTICO UnB\n"
    txt += "=" * 80 + "\n\n"
    
    txt += f"Data: {now}\n"
    txt += f"URL: {url}\n"
    txt += f"Ano: {report_year}\n"
    txt += "\n"
    
    # Agrupar por severity
    fail_issues = [i for i in issues if i["severity"] == "FAIL"]
    warn_issues = [i for i in issues if i["severity"] == "WARN"]
    pass_issues = [i for i in issues if i["severity"] == "PASS"]
    
    # Resumo
    txt += "-" * 80 + "\n"
    txt += "RESUMO\n"
    txt += "-" * 80 + "\n"
    txt += f"Erros Críticos (FAIL):    {len(fail_issues)}\n"
    txt += f"Avisos (WARN):            {len(warn_issues)}\n"
    txt += f"OK (PASS):                {len(pass_issues)}\n"
    txt += "\n\n"
    
    # Erros críticos
    if fail_issues:
        txt += "=" * 80 + "\n"
        txt += "❌ ERROS CRÍTICOS (FAIL)\n"
        txt += "=" * 80 + "\n\n"
        
        for idx, issue in enumerate(fail_issues, 1):
            txt += f"{idx}. {issue['issue']}\n"
            txt += f"   Tabela: {issue['table']}\n"
            txt += f"   Regra: {issue['rule']}\n"
            txt += f"   Detalhe: {issue['detail']}\n"
            txt += f"   Ação: {issue['recommendation']}\n"
            txt += "\n"
    
    # Avisos
    if warn_issues:
        txt += "=" * 80 + "\n"
        txt += "⚠️  AVISOS (WARN)\n"
        txt += "=" * 80 + "\n\n"
        
        for idx, issue in enumerate(warn_issues, 1):
            txt += f"{idx}. {issue['issue']}\n"
            txt += f"   Tabela: {issue['table']}\n"
            txt += f"   Regra: {issue['rule']}\n"
            txt += f"   Detalhe: {issue['detail']}\n"
            txt += f"   Ação: {issue['recommendation']}\n"
            txt += "\n"
    
    # OK
    if pass_issues:
        txt += "=" * 80 + "\n"
        txt += "✓ TUDO OK (PASS)\n"
        txt += "=" * 80 + "\n\n"
        
        for idx, issue in enumerate(pass_issues, 1):
            txt += f"{idx}. {issue['issue']}\n"
            txt += f"   {issue['detail']}\n"
            txt += "\n"
    
    txt += "\n" + "=" * 80 + "\n"
    txt += "FIM DO RELATÓRIO\n"
    txt += "=" * 80 + "\n"
    
    return txt

# ===== API =====

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria UnB</title>
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
        .button-group { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 20px; }
        button { padding: 12px; background: linear-gradient(135deg, #003366 0%, #2E1D86 100%); color: white; border: none; border-radius: 6px; font-weight: 600; cursor: pointer; }
        button:hover { opacity: 0.9; }
        button.secondary { background: #666; }
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
        .export-button:hover { opacity: 0.9; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <p class="subtitle">Análise de qualidade, consistência e regras específicas Capítulo 9</p>

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
            <div class="button-group">
                <button onclick="audit()">🔍 Executar Auditoria</button>
            </div>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Auditando...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 style="color: #003366; margin-bottom: 20px;">Discrepâncias que merecem atenção:</h2>
            <div id="content"></div>
            <button class="export-button" onclick="downloadReport()">📥 Baixar Relatório (TXT)</button>
        </div>
    </div>

    <script>
        let lastIssues = [];
        let lastUrl = '';
        let lastYear = 2025;

        async function audit() {
            const url = document.getElementById('url').value;
            const year = parseInt(document.getElementById('year').value);
            const base = parseInt(document.getElementById('baseYear').value);

            lastUrl = url;
            lastYear = year;

            document.getElementById('form').style.display = 'none';
            document.getElementById('loading').style.display = 'block';

            try {
                const res = await fetch('https://revisor-anuario-2.onrender.com/audit', {
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
                    <div class="rule-tag">Regra: ${i.rule}</div>
                </div>
            `).join('');

            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }

        function downloadReport() {
            // Gerar TXT e fazer download
            fetch('https://revisor-anuario-2.onrender.com/export/txt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    issues: lastIssues,
                    url: lastUrl,
                    report_year: lastYear
                })
            })
            .then(res => res.blob())
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `auditoria-anuario-${new Date().getTime()}.txt`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                a.remove();
            })
            .catch(e => alert('Erro ao baixar: ' + e.message));
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
    """Gera e retorna arquivo TXT com o relatório"""
    try:
        txt_content = generate_txt_report(
            data.get("issues", []),
            data.get("url", ""),
            data.get("report_year", 2025)
        )
        
        # Converter para bytes
        txt_bytes = txt_content.encode('utf-8')
        
        return StreamingResponse(
            iter([txt_bytes]),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=auditoria-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"}
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)