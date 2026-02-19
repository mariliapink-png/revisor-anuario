import re
from typing import List, Dict, Tuple, Optional, Any

import requests
from bs4 import BeautifulSoup
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel


# ============================================================
# FastAPI app (ISSO resolve o erro do Render: app.main:app)
# ============================================================

app = FastAPI(title="Auditoria Anuário UnB")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # se quiser restringir depois, dá.
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
    """
    Converte string numérica PT-BR para número (int/float).
    Retorna None se não numérico.
    """
    if not s or not isinstance(s, str):
        return None
    s = normalize_text(s)

    # remove % no final
    s = re.sub(r'[%]$', '', s).strip()

    # 1.234.567
    if re.match(r'^\d{1,3}(\.\d{3})+$', s):
        return int(s.replace('.', ''))

    # 1.234,56
    if re.match(r'^\d{1,3}(\.\d{3})*,\d+$', s):
        try:
            return float(s.replace('.', '').replace(',', '.'))
        except:
            return None

    # 1234,56
    if re.match(r'^\d+,\d+$', s):
        try:
            return float(s.replace(',', '.'))
        except:
            return None

    # 1234.56 (EN decimal)
    if re.match(r'^\d+\.\d+$', s):
        try:
            return float(s)
        except:
            return None

    # 1234
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
    """
    Baixa HTML. Importante: o anuário pode ser conteúdo estático já renderizado,
    mas se houver páginas com renderização forte via JS, requests pode pegar HTML incompleto.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
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
# Extração robusta de tabelas + contexto
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

        # headers
        headers = []
        thead = table_elem.find("thead")
        if thead:
            headers = [normalize_text(th.get_text(" ", strip=True)) for th in thead.find_all("th")]
        else:
            # fallback: primeira linha com th
            first_tr = table_elem.find("tr")
            if first_tr:
                ths = first_tr.find_all("th")
                if ths:
                    headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths]

        # rows
        rows_raw = []
        tbody = table_elem.find("tbody") or table_elem
        for tr in tbody.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
            if any(c != "" for c in cells):
                rows_raw.append(cells)

        # contexto após tabela (para "Fonte:" fora/itálico)
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
    """
    Encontra "Fonte:" dentro do HTML da tabela OU no texto logo abaixo (around_text),
    incluindo <i>/<em> (porque BeautifulSoup get_text pega itálico também).
    """
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
# Checklist: regras
# ============================================================

# 1) Fonte presente (não falhar por itálico)
def rule_missing_source(table: Dict) -> Optional[Dict]:
    src = find_source_text(table)
    if not src:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "missing_source",
            "issue": "Fonte não identificada",
            "detail": "Não foi encontrado 'Fonte:' dentro da tabela nem no rodapé imediatamente abaixo.",
            "recommendation": "Garantir 'Fonte: ...' no rodapé da tabela/gráfico/figura."
        }
    return None

# 2) Células em branco
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
            "detail": f"Exemplos (linha,coluna): {', '.join([f'({r},{c})' for r,c in blanks])}",
            "recommendation": "Preencher ou justificar campos vazios (ou usar '0' quando aplicável)."
        }
    return None

# 3) Ano-base (2024) deve aparecer em título/cabeçalho/legendas da tabela (com tolerância para séries)
def rule_year_base_mismatch(table: Dict, base_year: int) -> Optional[Dict]:
    name = normalize_text(table.get("nome", ""))
    headers = [normalize_text(h) for h in table.get("headers", [])]
    body_text = " ".join([name] + headers)

    years = set(re.findall(r'\b(20\d{2})\b', body_text))
    if not years:
        return None

    # se não inclui o ano-base, provável problema (a menos que seja tabela "conceitual" sem ano, mas aí nem teria anos)
    if str(base_year) not in years:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "year_base_mismatch",
            "issue": f"Ano-base ({base_year}) não aparece",
            "detail": f"Anos detectados no título/cabeçalho: {', '.join(sorted(years))}",
            "recommendation": f"Atualizar título/cabeçalho/legenda para refletir o ano-base {base_year} (ou série incluindo {base_year})."
        }

    # resíduo típico: título “..., 2023” em anuário ano-base 2024
    if "2023" in years and not has_time_series_indicator(name) and str(base_year) in years:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "year_possible_residue",
            "issue": "Possível resíduo de ano no título/cabeçalho",
            "detail": f"Foram detectados 2023 e {base_year} em tabela que não parece série histórica.",
            "recommendation": "Confirmar se o texto foi realmente atualizado."
        }

    return None

# 4) Separadores (milhar/decimal)
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
            "issue": "Mistura de separadores decimais (',' e '.')",
            "detail": f"Padrões detectados: {counts}",
            "recommendation": "Padronizar decimal PT-BR (vírgula)."
        }

    if counts["pt_milhar"] > 0 and counts["plain_int"] > 0:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "inconsistent_thousand_separator",
            "issue": "Padronização de milhar inconsistente",
            "detail": f"Padrões detectados: {counts}",
            "recommendation": "Escolher um padrão (ex.: sempre 1.234 para milhar) e aplicar no capítulo."
        }

    return None

# 5) Tabela vazia/zerada
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

# 6) Divergência de totais (só quando há linha Total e colunas numéricas)
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

    n_cols = max(len(r) for r in rows)
    # colunas numéricas candidatas (ignora col 0)
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

        tol = max(1.0, abs(s) * 0.005)  # 0,5% ou 1 unidade
        if abs(s - float(total_val)) > tol:
            return {
                "severity": "FAIL",
                "table": table["nome"],
                "rule": "totals_divergence",
                "issue": "Divergência em total",
                "detail": f"Coluna {c+1}: soma={s:.2f} vs total={float(total_val):.2f} (dif={s-float(total_val):+.2f})",
                "recommendation": "Recalcular total e revisar linhas incluídas/excluídas."
            }

    return None

# 7) Variações abruptas em séries históricas (HORIZONTAL, na MESMA LINHA)
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

            # thresholds (ajuste se quiser)
            if abs(var_pct) >= 500:
                return {
                    "severity": "FAIL",
                    "table": table["nome"],
                    "rule": "extreme_year_variation",
                    "issue": "Aumento/queda abrupta (série histórica)",
                    "detail": f"Categoria '{categoria}': {y0}={v0:g} → {y1}={v1:g} ({var_pct:+.1f}%)",
                    "recommendation": "Checar erro de digitação, mudança de critério, ou dado faltante em um dos anos."
                }
            elif abs(var_pct) >= 200:
                return {
                    "severity": "WARN",
                    "table": table["nome"],
                    "rule": "sharp_year_variation",
                    "issue": "Variação expressiva (série histórica)",
                    "detail": f"Categoria '{categoria}': {y0}={v0:g} → {y1}={v1:g} ({var_pct:+.1f}%)",
                    "recommendation": "Validar com a fonte (pode ser efeito real)."
                }

    return None

# 8) Duplicidades estruturais (SÓ se rótulo igual + valores iguais)
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
                "detail": f"Linhas {seen[key]+1} e {idx+1} com mesmo rótulo e mesmos valores.",
                "recommendation": "Verificar se houve repetição por erro de geração/edição."
            }

        seen[key] = idx

    return None


# ============================================================
# Siglas / padrões de nomenclatura / caixa alta / abreviações
# (você pode expandir essa lista quando quiser)
# ============================================================

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

    total_forms = set()
    for row in rows:
        if row:
            lab = normalize_text(row[0])
            if lab.lower().startswith("total"):
                total_forms.add(lab)

    if suspects:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "unknown_acronyms",
            "issue": "Siglas fora do padrão (não estão na lista oficial)",
            "detail": f"Exemplos: {', '.join(sorted(list(suspects))[:20])}",
            "recommendation": "Padronizar siglas no texto ou adicionar à lista oficial."
        }

    if len(total_forms) >= 2:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "inconsistent_uppercase_total",
            "issue": "Inconsistência de caixa alta (Total/TOTAL)",
            "detail": f"Formas encontradas: {', '.join(sorted(total_forms))}",
            "recommendation": "Padronizar (ex.: sempre 'Total' ou sempre 'TOTAL')."
        }

    return None


# ============================================================
# Análise de tabela (pipeline)
# ============================================================

def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues = []

    issue = rule_table_empty_or_all_zero(table)
    if issue:
        issues.append(issue)
        # mesmo vazia: vale apontar fonte/ano, porque é revisão editorial
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
# Auditoria completa
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
            "detail": f"Status={diag.get('status')} HTTP={diag.get('http_status')}",
            "recommendation": "Verificar URL; se a página renderiza via JS pesado, pode precisar de extração diferente."
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


def generate_txt_report(issues: List[Dict], url: str, report_year: int, base_year: int) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    txt = "=" * 80 + "\nAUDITORIA DO ANUÁRIO ESTATÍSTICO UnB\n" + "=" * 80 + "\n\n"
    txt += f"Data: {now}\nURL: {url}\nAno do Anuário: {report_year}\nAno-base: {base_year}\n\n"

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
# Rotas
# ============================================================

@app.get("/", response_class=HTMLResponse)
def root():
    # Para não quebrar seu iframe: mensagem clara e simples
    return """
    <h2>Backend OK ✅</h2>
    <p>Use o frontend separado (ex.: seu <code>index.html</code>) ou chame:</p>
    <ul>
      <li><code>POST /audit</code></li>
      <li><code>POST /export/txt</code></li>
      <li><code>GET /health</code></li>
    </ul>
    """

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
