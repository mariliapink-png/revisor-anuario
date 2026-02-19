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
        return html, {
            "tamanho_html_kb": len(html) / 1024,
            "contagem_tables": len(tables),
            "status": "OK",
        }
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
        around_text = []
        sib = table_elem
        for _ in range(8):
            sib = sib.find_next_sibling()
            if not sib:
                break
            txt = normalize_text(sib.get_text(" ", strip=True))
            if txt:
                around_text.append(txt)
        tables.append({
            "numero": table_idx,
            "nome": table_name,
            "headers": headers,
            "rows_raw": rows_raw,
            "html": str(table_elem),
            "around_text": around_text,
        })
    return tables

# ============================================================
# REGRAS ESPECÍFICAS
# ============================================================

def rule_blank_cells(table: Dict) -> Optional[Dict]:
    """Apontar células em branco"""
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    blanks = []
    for r_i, row in enumerate(rows, 1):
        for c_i, cell in enumerate(row, 1):
            if normalize_text(cell) == "":
                blanks.append((r_i, c_i))
                if len(blanks) >= 10:
                    break
        if len(blanks) >= 10:
            break
    if blanks:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "blank_cells",
            "issue": "Células em branco detectadas",
            "detail": f"Exemplos: {', '.join([f'(L{r},C{c})' for r,c in blanks[:8]])}",
            "recommendation": "Preencher ou justificar campos vazios."
        }
    return None

def rule_year_2024_check(table: Dict, base_year: int) -> Optional[Dict]:
    """Verificar se tabela/gráfico tem dados de 2024 e não de anos anteriores"""
    name = normalize_text(table.get("nome", ""))
    headers = [normalize_text(h) for h in table.get("headers", [])]
    body_text = " ".join([name] + headers)
    
    years = set(re.findall(r'\b(20\d{2})\b', body_text))
    if not years:
        return None
    
    # Se tem 2024, OK
    if str(base_year) in years:
        # Mas se tem TAMBÉM 2023/2022 sem ser série, alertar
        prev_years = [y for y in years if int(y) < base_year]
        if prev_years and len(years) == len(prev_years) + 1:
            # Série de tempo OK
            return None
        elif prev_years:
            return {
                "severity": "WARN",
                "table": table["nome"],
                "rule": "outdated_data_alongside_2024",
                "issue": "Dados de anos anteriores aparecem junto a 2024",
                "detail": f"Anos detectados: {', '.join(sorted(years))}",
                "recommendation": "Confirmar se deve incluir anos anteriores ou usar apenas 2024."
            }
        return None
    
    # Se não tem 2024, FAIL
    if years:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "missing_current_year",
            "issue": f"Ano-base {base_year} não encontrado",
            "detail": f"Anos detectados: {', '.join(sorted(years))}",
            "recommendation": f"Atualizar tabela/gráfico com dados de {base_year}."
        }
    return None

def rule_thousand_separator_consistency(table: Dict) -> Optional[Dict]:
    """Apontar inconsistência de separador de milhar (dentro da mesma tabela)"""
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    
    has_sep = 0  # com ponto (1.234)
    no_sep = 0   # sem ponto (1234)
    
    for row in rows[:60]:
        for cell in row:
            c = normalize_text(cell)
            if re.match(r'^\d{1,3}(\.\d{3})+$', c):
                has_sep += 1
            elif re.match(r'^\d{4,}$', c):
                no_sep += 1
    
    if has_sep > 0 and no_sep > 5:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "thousand_separator_inconsistency",
            "issue": "Separador de milhar inconsistente na tabela",
            "detail": f"Alguns números com ponto (1.234) e outros sem (1234)",
            "recommendation": "Padronizar: sempre usar separador de milhar (1.234) ou nunca usar."
        }
    return None

def rule_table_all_zero(table: Dict) -> Optional[Dict]:
    """Tabelas integralmente zeradas"""
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    
    has_nonzero = False
    for row in rows:
        for cell in row[1:]:
            v = parse_number_ptbr(cell)
            if v is not None and v != 0:
                has_nonzero = True
                break
        if has_nonzero:
            break
    
    if not has_nonzero:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "table_all_zero",
            "issue": "Tabela integralmente zerada",
            "detail": "Não há valores numéricos diferentes de zero.",
            "recommendation": "Confirmar se dados deveriam existir; se sim, revisar extração."
        }
    return None

def rule_totals_divergence(table: Dict) -> Optional[Dict]:
    """Divergência de totais - APENAS se linha Total existe"""
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 3:
        return None
    
    total_idx = None
    for i, row in enumerate(rows):
        if row and re.match(r'^\s*total\b', normalize_text(row[0]), flags=re.IGNORECASE):
            total_idx = i
            break
    
    if total_idx is None or total_idx < 2:
        return None  # Precisa de pelo menos 2 linhas antes do total
    
    n_cols = max(len(r) for r in rows) if rows else 0
    numeric_cols = []
    for c in range(1, min(n_cols, 6)):  # Apenas primeiras colunas
        vals = 0
        for r in rows[:total_idx]:
            if c < len(r) and parse_number_ptbr(r[c]) is not None:
                vals += 1
        if vals >= 2:
            numeric_cols.append(c)
    
    if not numeric_cols:
        return None
    
    for c in numeric_cols:
        s = 0.0
        for r in rows[:total_idx]:
            if c < len(r):
                v = parse_number_ptbr(r[c])
                if v is not None:
                    s += float(v)
        
        total_cell = rows[total_idx][c] if c < len(rows[total_idx]) else ""
        total_val = parse_number_ptbr(total_cell)
        if total_val is None:
            continue
        
        tol = max(2.0, abs(s) * 0.01)  # 1% ou 2 unidades
        if abs(s - float(total_val)) > tol:
            return {
                "severity": "FAIL",
                "table": table["nome"],
                "rule": "totals_divergence",
                "issue": f"Divergência em total (coluna {c+1})",
                "detail": f"Soma das linhas: {s:.1f} | Total declarado: {float(total_val):.1f}",
                "recommendation": "Revisar cálculo do total ou linhas incluídas."
            }
    
    return None

def rule_sharp_variation_in_series(table: Dict) -> Optional[Dict]:
    """Quedas abruptas (>50%) ou aumentos expressivos (>200%) em séries temporais"""
    headers = table.get("headers", [])
    rows = table.get("rows_raw", [])
    name = table.get("nome", "")
    
    if not rows or len(rows) < 2:
        return None
    
    year_cols = []
    for col_idx, header in enumerate(headers):
        h = normalize_text(header)
        if re.match(r'^20\d{2}$', h):
            year_cols.append((col_idx, h))
    
    if len(year_cols) < 2:
        return None  # Não é série temporal
    
    for r_i, row in enumerate(rows, 1):
        categoria = normalize_text(row[0]) if row else f"Linha {r_i}"
        values = []
        for col_idx, year in year_cols:
            if col_idx < len(row):
                v = parse_number_ptbr(row[col_idx])
                if v is not None and v > 0:
                    values.append((year, float(v)))
        
        if len(values) < 2:
            continue
        
        for i in range(len(values) - 1):
            y0, v0 = values[i]
            y1, v1 = values[i + 1]
            
            if v0 == 0:
                continue
            
            var_pct = ((v1 - v0) / v0) * 100
            
            if var_pct < -50:  # Queda > 50%
                return {
                    "severity": "WARN",
                    "table": table["nome"],
                    "rule": "sharp_drop",
                    "issue": f"Queda abrupta detectada (>{abs(var_pct):.0f}%)",
                    "detail": f"'{categoria}': {y0}={v0:g} → {y1}={v1:g} ({var_pct:+.1f}%)",
                    "recommendation": "Validar: mudança de critério, exclusões ou erro de extração?"
                }
            
            if var_pct > 200:  # Aumento > 200%
                return {
                    "severity": "WARN",
                    "table": table["nome"],
                    "rule": "sharp_increase",
                    "issue": f"Aumento expressivo detectado (>{var_pct:.0f}%)",
                    "detail": f"'{categoria}': {y0}={v0:g} → {y1}={v1:g} ({var_pct:+.1f}%)",
                    "recommendation": "Validar com a fonte: pode ser efeito real."
                }
    
    return None

def rule_duplicated_rows_same_label_only(table: Dict) -> Optional[Dict]:
    """Duplicidade estrutural - APENAS se label/nome for IGUAL"""
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 2:
        return None
    
    label_values = {}
    for idx, row in enumerate(rows):
        if not row:
            continue
        label = normalize_text(row[0]).lower()
        if not label:
            continue
        sig = tuple(parse_number_ptbr(cell) for cell in row[1:])
        
        if label in label_values and label_values[label] != sig:
            continue  # Labels iguais mas valores diferentes = OK
        
        if label in label_values and label_values[label] == sig:
            # Labels iguais E valores iguais = erro
            return {
                "severity": "WARN",
                "table": table["nome"],
                "rule": "duplicated_rows_identical",
                "issue": "Linha duplicada detectada",
                "detail": f"Rótulo '{row[0]}' aparece duplicado com mesmos valores.",
                "recommendation": "Remover duplicação."
            }
        
        label_values[label] = sig
    
    return None

def rule_spelling_errors(table: Dict) -> Optional[Dict]:
    """Erros de ortografia/digitação comuns em PT-BR"""
    name = table.get("nome", "")
    headers = [normalize_text(h) for h in table.get("headers", [])]
    rows = table.get("rows_raw", [])
    
    text = " ".join([name] + headers)
    for row in rows[:20]:
        text += " " + " ".join(row)
    
    # Alguns erros comuns
    errors = {
        r'\bobrigatatio\b': ('obrigatório', 'digitação'),
        r'\bregulamente\b': ('regularmente', 'digitação'),
        r'\bhabiiltação\b': ('habilitação', 'digitação'),
        r'\bmunicipio\b': ('município', 'acentuação'),
    }
    
    for pattern, (correct, type_) in errors.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            return {
                "severity": "WARN",
                "table": table["nome"],
                "rule": "spelling_error",
                "issue": f"Erro de {type_} detectado",
                "detail": f"Encontrado padrão suspeito; verificar '{correct}'.",
                "recommendation": "Corrigir ortografia/digitação."
            }
    
    return None

def rule_inconsistent_uppercase(table: Dict) -> Optional[Dict]:
    """Inconsistência de caixa alta (TOTAL vs Total vs total)"""
    rows = table.get("rows_raw", [])
    
    if not rows:
        return None
    
    total_forms = set()
    for row in rows:
        if row:
            label = row[0]
            if label and re.search(r'\btotal\b', normalize_text(label), flags=re.IGNORECASE):
                total_forms.add(label)
    
    if len(total_forms) >= 2:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "inconsistent_uppercase",
            "issue": "Inconsistência de caixa alta em 'Total'",
            "detail": f"Formas encontradas: {', '.join(sorted(list(total_forms)[:5]))}",
            "recommendation": "Padronizar (ex.: sempre 'Total')."
        }
    
    return None

# ============================================================
# PIPELINE
# ============================================================

def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues = []
    
    # Parar se tabela vazia
    issue = rule_table_all_zero(table)
    if issue:
        issues.append(issue)
        return issues
    
    # Aplicar regras
    for rule in (
        rule_blank_cells,
        lambda t: rule_year_2024_check(t, base_year),
        rule_thousand_separator_consistency,
        rule_totals_divergence,
        rule_sharp_variation_in_series,
        rule_duplicated_rows_same_label_only,
        rule_spelling_errors,
        rule_inconsistent_uppercase,
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
            "recommendation": "Verificar URL ou renderização."
        })
        return issues
    
    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "rule": "scan_ok",
        "issue": f"✓ {diag['contagem_tables']} tabela(s) encontrada(s)",
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
        <p class="subtitle">Análise de qualidade, consistência e conformidade de dados</p>

        <div id="form">
            <div class="form-group">
                <label>URL do Anuário</label>
                <input type="url" id="url" value="https://anuariounb2025.netlify.app/">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Ano do Anuário</label>
                    <input type="number" id="year" value="2025">
                </div>
                <div class="form-group">
                    <label>Ano-base</label>
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
            <h2 style="color: #003366; margin-bottom: 20px;">Resultados:</h2>
            <div id="content"></div>
            <button class="export-button" onclick="downloadReport()">📥 Baixar Relatório (TXT)</button>
        </div>
    </div>

    <script>
        let lastIssues = [], lastUrl = '', lastYear = 2025, lastBase = 2024;

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
        report_year=int(data.get("report_year", 2025)),
        base_year=int(data.get("base_year", 2024)),
    )
    return StreamingResponse(
        iter([txt.encode("utf-8")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=auditoria-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"},
    )
