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


# ============================================================
# Normalização / parsing
# ============================================================

WEIRD_CHARS_RE = re.compile(r'[\u0000-\u001f\u007f\uFFFD\u00AD\u200B\u200E\u200F\u2028\u2029]')

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = s.replace("\xa0", " ").replace("\u00a0", " ")
    s = WEIRD_CHARS_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_number_ptbr(s: str) -> Optional[Any]:
    """Converte string numérica PT-BR para número (int/float)."""
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


# ============================================================
# Download HTML
# ============================================================

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
            "http_status": resp.status_code,
        }
    except Exception as e:
        return "", {"tamanho_html_kb": 0, "contagem_tables": 0, "status": f"ERRO: {str(e)}"}


# ============================================================
# Extração de tabelas
# ============================================================

def extract_tables_from_html(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    tables = []

    all_tables = soup.find_all("table")
    for table_idx, table_elem in enumerate(all_tables, 1):
        caption = table_elem.find("caption")
        table_name = normalize_text(caption.get_text(" ", strip=True)) if caption else ""
        if not table_name:
            table_name = normalize_text(table_elem.get("aria-label", "")) or f"Tabela {table_idx}"

        headers = []
        thead = table_elem.find("thead")
        if thead:
            headers = [normalize_text(th.get_text(" ", strip=True)) for th in thead.find_all("th")]
        else:
            first_tr = table_elem.find("tr")
            if first_tr:
                ths = first_tr.find_all("th")
                if ths:
                    headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths]

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
# Detectores auxiliares
# ============================================================

def detect_year_columns(headers: List[str]) -> List[int]:
    year_cols = []
    for col_idx, header in enumerate(headers):
        h = normalize_text(header)
        if re.match(r'^20\d{2}$', h):
            year_cols.append(col_idx)
    return year_cols

def has_time_series_indicator(text: str) -> bool:
    t = normalize_text(text).lower()
    indicators = [
        "série", "evolução", "históric", "trend", "período", "temporal",
        r'20\d{2}\s+a\s+20\d{2}',
        r'20\d{2}\s*-\s*20\d{2}'
    ]
    return any(re.search(p, t) for p in indicators)

def find_source_text(table: Dict) -> Optional[str]:
    html = table.get("html", "")
    soup = BeautifulSoup(html, "html.parser")
    inside_txt = normalize_text(soup.get_text(" ", strip=True))

    m = re.search(r'\bFonte\s*:\s*(.+)', inside_txt, flags=re.IGNORECASE)
    if m:
        return normalize_text("Fonte: " + m.group(1))

    for block in table.get("around_text", []):
        m2 = re.search(r'\bFonte\s*:\s*(.+)', block, flags=re.IGNORECASE)
        if m2:
            return normalize_text("Fonte: " + m2.group(1))

    return None


# ============================================================
# Regras de validação
# ============================================================

def rule_missing_source(table: Dict) -> Optional[Dict]:
    src = find_source_text(table)
    if not src:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "missing_source",
            "issue": "Fonte não identificada",
            "detail": "Não foi encontrado 'Fonte:' dentro da tabela nem no rodapé abaixo.",
            "recommendation": "Garantir 'Fonte: ...' no rodapé da tabela/figura."
        }
    return None

def rule_blank_cells(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    blanks = []
    for r_i, row in enumerate(rows, 1):
        for c_i, cell in enumerate(row, 1):
            if normalize_text(cell) == "":
                blanks.append((r_i, c_i))
                if len(blanks) >= 15:
                    break
        if len(blanks) >= 15:
            break
    if blanks:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "blank_cells",
            "issue": "Células em branco",
            "detail": f"Exemplos (linha,coluna): {', '.join([f'({r},{c})' for r,c in blanks[:10]])}",
            "recommendation": "Preencher ou justificar campos vazios."
        }
    return None

def rule_year_base_mismatch(table: Dict, base_year: int) -> Optional[Dict]:
    name = normalize_text(table.get("nome", ""))
    headers = [normalize_text(h) for h in table.get("headers", [])]
    body_text = " ".join([name] + headers)

    years = set(re.findall(r'\b(20\d{2})\b', body_text))
    if not years:
        return None

    if str(base_year) not in years:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "year_base_mismatch",
            "issue": f"Ano-base ({base_year}) não aparece",
            "detail": f"Anos detectados: {', '.join(sorted(years))}",
            "recommendation": f"Atualizar título/cabeçalho para refletir ano-base {base_year}."
        }
    return None

def rule_separator_standardization(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return None

    counts = {"pt_milhar": 0, "pt_decimal": 0, "plain_int": 0, "en_decimal": 0}
    sample = 0

    for row in rows[:80]:
        for cell in row:
            c = normalize_text(cell)
            if not c:
                continue
            if re.match(r'^\d{1,3}(\.\d{3})+$', c):
                counts["pt_milhar"] += 1; sample += 1
            elif re.match(r'^\d{1,3}(\.\d{3})*,\d+$', c) or re.match(r'^\d+,\d+$', c):
                counts["pt_decimal"] += 1; sample += 1
            elif re.match(r'^\d+\.\d+$', c):
                counts["en_decimal"] += 1; sample += 1
            elif re.match(r'^\d+$', c):
                counts["plain_int"] += 1; sample += 1

    if sample < 8:
        return None

    if counts["en_decimal"] > 0 and counts["pt_decimal"] > 0:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "mixed_decimal_separators",
            "issue": "Mistura de separadores decimais",
            "detail": f"Padrões: {counts}",
            "recommendation": "Padronizar decimal PT-BR (vírgula)."
        }

    if counts["pt_milhar"] > 0 and counts["plain_int"] > 0:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "inconsistent_thousand_separator",
            "issue": "Padronização de milhar inconsistente",
            "detail": f"Padrões: {counts}",
            "recommendation": "Padronizar milhar (sempre 1.234)."
        }

    return None

def rule_table_empty_or_all_zero(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "table_empty",
            "issue": "Tabela vazia",
            "detail": "Sem linhas de dados.",
            "recommendation": "Inserir dados ou remover tabela."
        }

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
            "detail": "Sem valores numéricos diferentes de zero.",
            "recommendation": "Revisar extração/consulta dos dados."
        }

    return None

def rule_totals_divergence(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 3:
        return None

    total_idx = None
    for i, row in enumerate(rows):
        if row and re.match(r'^\s*total\b', normalize_text(row[0]), flags=re.IGNORECASE):
            total_idx = i
            break
    if total_idx is None or total_idx == 0:
        return None

    n_cols = max(len(r) for r in rows) if rows else 0
    numeric_cols = []
    for c in range(1, n_cols):
        vals = 0
        for r in rows[:total_idx]:
            if c < len(r) and parse_number_ptbr(r[c]) is not None:
                vals += 1
        if vals >= 2:
            numeric_cols.append(c)

    if not numeric_cols:
        return None

    for c in numeric_cols[:12]:
        s = 0.0
        any_val = False
        for r in rows[:total_idx]:
            if c < len(r):
                v = parse_number_ptbr(r[c])
                if v is not None:
                    s += float(v)
                    any_val = True
        if not any_val:
            continue

        total_cell = rows[total_idx][c] if c < len(rows[total_idx]) else ""
        total_val = parse_number_ptbr(total_cell)
        if total_val is None:
            continue

        tol = max(1.0, abs(s) * 0.005)
        if abs(s - float(total_val)) > tol:
            return {
                "severity": "FAIL",
                "table": table["nome"],
                "rule": "totals_divergence",
                "issue": "Divergência em total",
                "detail": f"Coluna {c+1}: soma={s:.2f} vs total={float(total_val):.2f}",
                "recommendation": "Recalcular total e revisar."
            }

    return None

def rule_extreme_year_variation(table: Dict) -> Optional[Dict]:
    headers = table.get("headers", [])
    rows = table.get("rows_raw", [])
    name = table.get("nome", "")

    if not rows:
        return None

    year_cols = detect_year_columns(headers)
    is_ts = (len(year_cols) >= 3) or has_time_series_indicator(name)
    if not is_ts or len(year_cols) < 2:
        return None

    for r_i, row in enumerate(rows, 1):
        categoria = normalize_text(row[0]) if row else f"Linha {r_i}"
        values = []
        for c in year_cols:
            if c < len(row):
                v = parse_number_ptbr(row[c])
                if v is not None:
                    values.append((headers[c], float(v)))

        if len(values) < 2:
            continue

        for i in range(len(values) - 1):
            y0, v0 = values[i]
            y1, v1 = values[i + 1]
            if v0 == 0:
                continue

            var_pct = ((v1 - v0) / v0) * 100

            if abs(var_pct) >= 500:
                return {
                    "severity": "FAIL",
                    "table": table["nome"],
                    "rule": "extreme_year_variation",
                    "issue": "Aumento/queda abrupta (série histórica)",
                    "detail": f"Categoria '{categoria}': {y0}={v0:g} → {y1}={v1:g} ({var_pct:+.1f}%)",
                    "recommendation": "Checar erro de digitação ou mudança de critério."
                }
            elif abs(var_pct) >= 200:
                return {
                    "severity": "WARN",
                    "table": table["nome"],
                    "rule": "sharp_year_variation",
                    "issue": "Variação expressiva (série histórica)",
                    "detail": f"Categoria '{categoria}': {y0}={v0:g} → {y1}={v1:g} ({var_pct:+.1f}%)",
                    "recommendation": "Validar com a fonte."
                }

    return None

def rule_duplicated_rows_strict(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 2:
        return None

    seen = {}
    for idx, row in enumerate(rows):
        if not row:
            continue
        label = normalize_text(row[0]).lower()
        sig = tuple(parse_number_ptbr(cell) for cell in row[1:])
        key = (label, sig)

        if label and key in seen:
            return {
                "severity": "WARN",
                "table": table["nome"],
                "rule": "duplicated_rows_strict",
                "issue": "Possível duplicidade estrutural",
                "detail": f"Linhas {seen[key]+1} e {idx+1} com mesmo rótulo e valores.",
                "recommendation": "Verificar se houve repetição por erro."
            }

        seen[key] = idx

    return None

SIGLAS_ALLOWLIST = {
    "CEPE","CEG","CEX","CPP","CCD","CAD","CAC","CGP","CPLAD","CAPRO","CDH",
    "VRT","OUV","PF","AUD","AAMC","GRE","PRC","INFRA","SEMA","SPI",
    "DEG","DAIA","DIEG","DTG","DAPLI","DEX","DTE","DDIS","DDC","DPG","DIRIC","DIRPG",
    "DPI","DIRPE","DPA","CDT","DAC","DDS","DEAC","DASU","DRU","DACES","DGP","DCADE","DAP","DSO","DPAM",
    "DAF","DIMEX","DACP","DGM","DCF","DCO","DCA","DPO","DPL","DOR","DPR","DAI",
    "BCE","STI","EDU","FAL","HUB","ACE","PCTec","UnB-TV","CEAD","SAA","SECOM","INT","CERI","SOC","SDH"
}
SIGLA_TOKEN_RE = re.compile(r'\b[A-Z]{2,6}\b')

def rule_acronyms_and_naming(table: Dict) -> Optional[Dict]:
    headers = [normalize_text(h) for h in table.get("headers", [])]
    rows = table.get("rows_raw", [])

    text_pool = " ".join(headers)
    for row in rows[:120]:
        if row:
            text_pool += " " + normalize_text(row[0])

    suspects = set()
    for tok in SIGLA_TOKEN_RE.findall(text_pool):
        if tok not in SIGLAS_ALLOWLIST:
            suspects.add(tok)

    if suspects:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "unknown_acronyms",
            "issue": "Siglas fora do padrão",
            "detail": f"Exemplos: {', '.join(sorted(list(suspects))[:20])}",
            "recommendation": "Padronizar siglas."
        }

    return None


# ============================================================
# Análise de tabela
# ============================================================

def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues = []

    issue = rule_table_empty_or_all_zero(table)
    if issue:
        issues.append(issue)
        src_issue = rule_missing_source(table)
        if src_issue:
            issues.append(src_issue)
        year_issue = rule_year_base_mismatch(table, base_year)
        if year_issue:
            issues.append(year_issue)
        return issues

    for rule in (
        rule_missing_source,
        lambda t: rule_year_base_mismatch(t, base_year),
        rule_blank_cells,
        rule_separator_standardization,
        rule_totals_divergence,
        rule_extreme_year_variation,
        rule_duplicated_rows_strict,
        rule_acronyms_and_naming,
    ):
        out = rule(table)
        if out:
            issues.append(out)

    return issues


# ============================================================
# Auditoria
# ============================================================

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    issues = []
    html, diag = download_page(url)

    if diag.get("contagem_tables", 0) == 0:
        issues.append({
            "severity": "FAIL",
            "table": "Documento",
            "rule": "document",
            "issue": "Nenhuma tabela encontrada",
            "detail": f"Status={diag.get('status')}",
            "recommendation": "Verificar URL."
        })
        return issues

    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "rule": "document",
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


# ============================================================
# HTML FRONTEND (embutido)
# ============================================================

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
        <p class="subtitle">Análise de qualidade, consistência e conformidade</p>

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


# ============================================================
# Rotas
# ============================================================

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
