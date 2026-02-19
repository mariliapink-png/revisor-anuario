import re
from typing import List, Dict, Tuple, Optional, Any
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel


# =========================
# FastAPI app (OBRIGATÓRIO p/ Render: uvicorn app.main:app)
# =========================

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


# =========================
# Normalização / parsing
# =========================

WEIRD_CHARS_RE = re.compile(r'[\u0000-\u001f\u007f\uFFFD\u00AD\u200B\u200E\u200F\u2028\u2029]')

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = s.replace("\xa0", " ").replace("\u00a0", " ")
    s = WEIRD_CHARS_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()

def parse_number_ptbr(s: str) -> Optional[Any]:
    """Converte string numérica PT-BR para número (int/float). Retorna None se não numérico."""
    if not s or not isinstance(s, str):
        return None
    s = normalize_text(s)

    # Remove marcadores comuns (ex.: % no final)
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


# =========================
# Download HTML
# =========================

def download_page(url: str) -> Tuple[str, Dict]:
    """Baixa HTML com headers realistas"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    try:
        resp = requests.get(url, timeout=25, headers=headers)
        resp.raise_for_status()
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


# =========================
# Extração robusta de tabelas
# =========================

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
        for _ in range(7):  # aumentei um pouco o alcance
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


# =========================
# Detectores auxiliares
# =========================

def detect_year_columns(headers: List[str]) -> List[int]:
    year_cols = []
    for col_idx, header in enumerate(headers):
        h = normalize_text(header)
        if re.match(r'^20\d{2}$', h):
            year_cols.append(col_idx)
    return year_cols

def has_time_series_indicator(table_name: str) -> bool:
    t = normalize_text(table_name).lower()
    indicators = [
        "série", "evolução", "históric", "trend", "período", "temporal",
        r"20\d{2}\s+a\s+20\d{2}", r"20\d{2}\s*-\s*20\d{2}"
    ]
    return any(re.search(p, t) for p in indicators)

def find_source_text(table: Dict) -> Optional[str]:
    """
    Encontra 'Fonte:' dentro do HTML da tabela OU no texto logo abaixo (around_text),
    incluindo casos em itálico (<i>/<em>).
    """
    html = table.get("html", "")
    soup = BeautifulSoup(html, "html.parser")
    inside_txt = normalize_text(soup.get_text(" ", strip=True))

    # NÃO use $ no fim: muitas vezes "Fonte:" não está no final da string extraída
    m = re.search(r"\bFonte\s*:\s*([^\n\r]+)", inside_txt, flags=re.IGNORECASE)
    if m:
        return normalize_text("Fonte: " + m.group(1))

    for block in table.get("around_text", []):
        m2 = re.search(r"\bFonte\s*:\s*([^\n\r]+)", block, flags=re.IGNORECASE)
        if m2:
            return normalize_text("Fonte: " + m2.group(1))

    return None


# =========================
# Regras
# =========================

def rule_missing_source(table: Dict) -> Optional[Dict]:
    if not find_source_text(table):
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "missing_source",
            "issue": "Fonte não identificada",
            "detail": "Não foi encontrado 'Fonte:' dentro da tabela nem no rodapé imediatamente abaixo.",
            "recommendation": "Adicionar/garantir 'Fonte: ...' no rodapé (mesmo em itálico)."
        }
    return None

def rule_blank_cells(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    blanks = []
    for r_i, row in enumerate(rows, 1):
        for c_i, cell in enumerate(row, 1):
            if cell == "":
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
            "issue": "Células em branco",
            "detail": f"Exemplos (linha,coluna): {', '.join([f'({r},{c})' for r,c in blanks])}",
            "recommendation": "Preencher ou justificar campos vazios (ou usar '0' quando aplicável)."
        }
    return None

def rule_year_base_mismatch(table: Dict, base_year: int) -> Optional[Dict]:
    name = normalize_text(table.get("nome", ""))
    headers = [normalize_text(h) for h in table.get("headers", [])]
    years_in_name = set(re.findall(r"\b(20\d{2})\b", name))
    years_in_headers = set(re.findall(r"\b(20\d{2})\b", " ".join(headers)))
    all_years = years_in_name.union(years_in_headers)
    if not all_years:
        return None

    if str(base_year) not in all_years:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "year_base_mismatch",
            "issue": f"Ano-base ({base_year}) não aparece na tabela",
            "detail": f"Anos detectados: {', '.join(sorted(all_years))}",
            "recommendation": f"Revisar título/cabeçalho/legendas para refletir o ano-base {base_year}."
        }
    return None

def rule_thousand_separator_inconsistency(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return None

    patterns = {"pt_milhar": 0, "pt_decimal": 0, "plain_int": 0, "en_decimal": 0}
    sample = 0

    for row in rows[:50]:
        for cell in row:
            c = normalize_text(cell)
            if not c:
                continue
            if re.match(r"^\d{1,3}(\.\d{3})+$", c):
                patterns["pt_milhar"] += 1; sample += 1
            elif re.match(r"^\d{1,3}(\.\d{3})*,\d+$", c) or re.match(r"^\d+,\d+$", c):
                patterns["pt_decimal"] += 1; sample += 1
            elif re.match(r"^\d+\.\d+$", c):
                patterns["en_decimal"] += 1; sample += 1
            elif re.match(r"^\d+$", c):
                patterns["plain_int"] += 1; sample += 1

    if sample < 5:
        return None

    if patterns["en_decimal"] > 0 and patterns["pt_decimal"] > 0:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "separator_mixed_decimal",
            "issue": "Mistura de separadores decimais (',' e '.')",
            "detail": f"Padrões: {patterns}",
            "recommendation": "Padronizar decimais em PT-BR: usar vírgula para decimal (ex.: 1,23)."
        }

    if patterns["pt_milhar"] > 0 and patterns["plain_int"] > 0:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "separator_inconsistent_thousand",
            "issue": "Separador de milhar inconsistente",
            "detail": f"Padrões: {patterns}",
            "recommendation": "Padronizar milhares (ex.: sempre 1.234 ou sempre 1234)."
        }

    return None

def rule_totals_divergence(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 3:
        return None

    total_idx = None
    for i, row in enumerate(rows):
        if row and re.match(r"^(total)\b", normalize_text(row[0]), flags=re.IGNORECASE):
            total_idx = i
            break
    if total_idx is None:
        return None

    n_cols = max(len(r) for r in rows)
    numeric_cols = []
    for c in range(1, n_cols):
        nums = []
        for r in rows[:total_idx]:
            if c < len(r):
                v = parse_number_ptbr(r[c])
                if v is not None:
                    nums.append(v)
        if len(nums) >= 2:
            numeric_cols.append(c)

    if len(numeric_cols) < 1:
        return None

    for c in numeric_cols[:10]:
        s = 0
        any_val = False
        for r in rows[:total_idx]:
            if c < len(r):
                v = parse_number_ptbr(r[c])
                if v is not None:
                    s += v
                    any_val = True
        if not any_val:
            continue

        total_cell = rows[total_idx][c] if c < len(rows[total_idx]) else ""
        total_val = parse_number_ptbr(total_cell)
        if total_val is None:
            continue

        tol = max(1, abs(s) * 0.005)
        if abs(s - total_val) > tol:
            return {
                "severity": "FAIL",
                "table": table["nome"],
                "rule": "totals_divergence",
                "issue": "Divergência em total",
                "detail": f"Coluna {c+1}: soma={s} vs total={total_val} (dif={s-total_val:+.2f})",
                "recommendation": "Recalcular total e revisar linhas incluídas/excluídas."
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

    for row in rows:
        categoria = normalize_text(row[0]) if row else "Linha"
        values = []
        for c in year_cols:
            if c < len(row):
                v = parse_number_ptbr(row[c])
                if v is not None:
                    values.append((headers[c], v))

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
                    "issue": "Variação extrema (série histórica)",
                    "detail": f"Categoria '{categoria}': {y0}={v0} → {y1}={v1} ({var_pct:+.1f}%)",
                    "recommendation": "Checar extração, mudança de regra/definição, ou erro de digitação."
                }
            elif abs(var_pct) >= 200:
                return {
                    "severity": "WARN",
                    "table": table["nome"],
                    "rule": "sharp_year_variation",
                    "issue": "Variação expressiva (série histórica)",
                    "detail": f"Categoria '{categoria}': {y0}={v0} → {y1}={v1} ({var_pct:+.1f}%)",
                    "recommendation": "Validar com a fonte (pode ser efeito real)."
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
            "issue": "Tabela integralmente zerada (ou sem números válidos)",
            "detail": "Não foi encontrado valor numérico diferente de zero.",
            "recommendation": "Confirmar se deveria haver dados; se sim, revisar extração/consulta."
        }

    return None


# =========================
# Pipeline
# =========================

def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues = []

    issue = rule_table_empty_or_all_zero(table)
    if issue:
        issues.append(issue)
        # ainda checa fonte/ano
        src = rule_missing_source(table)
        if src:
            issues.append(src)
        yr = rule_year_base_mismatch(table, base_year)
        if yr:
            issues.append(yr)
        return issues

    for rule in (
        rule_missing_source,
        lambda t: rule_year_base_mismatch(t, base_year),
        rule_blank_cells,
        rule_thousand_separator_inconsistency,
        rule_totals_divergence,
        rule_extreme_year_variation,
    ):
        out = rule(table)
        if out:
            issues.append(out)

    return issues

def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    issues = []
    html, diag = download_page(url)

    if diag["contagem_tables"] == 0:
        issues.append({
            "severity": "FAIL",
            "table": "Documento",
            "rule": "document",
            "issue": "Nenhuma tabela",
            "detail": f"{diag['status']}",
            "recommendation": "Verificar URL e se o conteúdo é renderizado via JS."
        })
        return issues

    issues.append({
        "severity": "PASS",
        "table": "Documento",
        "rule": "document",
        "issue": f"✓ {diag['contagem_tables']} tabela(s) encontrada(s)",
        "detail": f"HTML: {diag['tamanho_html_kb']:.1f} KB",
        "recommendation": "Analisando tabelas..."
    })

    tables = extract_tables_from_html(html)
    for table in tables:
        issues.extend(analyze_table(table, base_year))

    return issues


# =========================
# Relatório TXT
# =========================

def generate_txt_report(issues: List[Dict], url: str, report_year: int, base_year: int) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    fail = [i for i in issues if i["severity"] == "FAIL"]
    warn = [i for i in issues if i["severity"] == "WARN"]
    passr = [i for i in issues if i["severity"] == "PASS"]

    txt = "=" * 80 + "\nAUDITORIA DO ANUÁRIO ESTATÍSTICO UnB\n" + "=" * 80 + "\n\n"
    txt += f"Data: {now}\nURL: {url}\nAno relatório: {report_year}\nAno-base: {base_year}\n\n"
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


# =========================
# Endpoints
# =========================

HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Auditoria UnB</title></head>
<body style="font-family:Arial;padding:20px">
<h2>Auditoria - Anuário UnB</h2>
<p>Backend OK. Use o frontend separado ou chame POST /audit.</p>
</body></html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML

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
        data.get("issues", []),
        data.get("url", ""),
        int(data.get("report_year", 2025)),
        int(data.get("base_year", 2024)),
    )
    return StreamingResponse(
        iter([txt.encode("utf-8")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=auditoria-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"}
    )

