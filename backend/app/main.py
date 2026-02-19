from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup, Tag
import re
import unicodedata
import hashlib
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

# =========================
# Models
# =========================
class AuditRequest(BaseModel):
    url: str
    report_year: int
    base_year: int


# =========================
# Helpers: text/normalize
# =========================
TOTAL_WORDS = {"total", "subtotal", "totais", "geral", "total geral"}
ND_WORDS = {"nd", "n.d.", "não disponível", "nao disponivel", "n/a", "na", "não informado", "nao informado"}
EMPTY_MARKERS = {"", "—", "–", "-", "―"}

SUSPECT_WORDS = {
    "estut", "administ", "gover", "regulamente", "instituído", "instituido",
    "administrist", "estud", "admnis", "regulament"
}

BROKEN_CHARS = [" ", " "]

SIGLA_RE = re.compile(r"\b[A-Z]{2,10}(?:[-/][A-Z0-9]{1,10})*\b")
SIGLA_NAME_RE = re.compile(r"^([A-Z]{2,10}(?:[-/][A-Z0-9]{1,10})*)\s*[-–—]\s*(.+)$")

YEAR_RE = re.compile(r"\b(20\d{2})\b")
YEAR_RANGE_RE = re.compile(r"\b(20\d{2})\s*(?:a|até|–|-)\s*(20\d{2})\b", re.IGNORECASE)

DECIMAL_DOT_RE = re.compile(r"\b\d+\.\d+\b")
THOUSAND_DOT_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
PTBR_DECIMAL_RE = re.compile(r"^\d{1,3}(\.\d{3})*,\d+$")

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def normalize_key(s: str) -> str:
    s = normalize_text(s).lower()
    s = strip_accents(s)
    s = re.sub(r"[^\w\s/-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def is_empty_cell(s: str) -> bool:
    t = normalize_text(s)
    if t in EMPTY_MARKERS:
        return True
    return False

def is_nd_cell(s: str) -> bool:
    t = normalize_key(s)
    return t in {normalize_key(x) for x in ND_WORDS}

def detect_years_in_text(text: str) -> List[int]:
    text = normalize_text(text)
    return [int(y) for y in YEAR_RE.findall(text)]

def detect_year_range(text: str) -> Optional[Tuple[int, int]]:
    text = normalize_text(text)
    m = YEAR_RANGE_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


# =========================
# Numeric parsing (PT-BR robust)
# =========================
def parse_number_ptbr(s: Any) -> Optional[float]:
    """
    Converte valores para número:
    - "8.415" => 8415 (int-like float)
    - "10.490,50" => 10490.50
    - "15,8%" => 15.8
    - " -  " => None
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)

    t = normalize_text(str(s))
    if t == "":
        return None
    if t in EMPTY_MARKERS:
        return None

    # remove percent sign, keep info elsewhere if needed
    t = t.replace("%", "").strip()

    # remove currency symbols
    t = re.sub(r"[R$\u20ac£]", "", t).strip()

    # sign
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1].strip()
    if t.startswith("-"):
        neg = True
        t = t[1:].strip()

    # thousand-dot only (ptbr integer)
    if THOUSAND_DOT_RE.match(t):
        v = float(int(t.replace(".", "")))
        return -v if neg else v

    # ptbr decimal
    if PTBR_DECIMAL_RE.match(t):
        try:
            v = float(t.replace(".", "").replace(",", "."))
            return -v if neg else v
        except:
            return None

    # plain integer
    if re.match(r"^\d+$", t):
        try:
            v = float(int(t))
            return -v if neg else v
        except:
            return None

    # decimal-dot (likely wrong locale, but parseable)
    if re.match(r"^\d+\.\d+$", t):
        try:
            v = float(t)
            return -v if neg else v
        except:
            return None

    return None


# =========================
# Download + extraction
# =========================
def download_page(url: str) -> Tuple[str, Dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    try:
        resp = requests.get(url, timeout=25, headers=headers)
        resp.encoding = "utf-8"
        html = resp.text or ""
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        figs = soup.find_all(["figure", "img"])
        return html, {
            "tamanho_html_kb": len(html) / 1024,
            "contagem_tables": len(tables),
            "contagem_figuras": len(figs),
            "status": "OK",
            "http_status": resp.status_code
        }
    except Exception as e:
        return "", {"tamanho_html_kb": 0, "contagem_tables": 0, "contagem_figuras": 0, "status": f"ERRO: {str(e)}"}


def collect_neighbor_text(node: Tag, max_prev: int = 5, max_next: int = 10) -> Tuple[str, str]:
    """
    Coleta texto antes/depois do node (irmãos) para achar Fonte/Notas.
    """
    before_parts = []
    after_parts = []

    # prev siblings
    cur = node
    for _ in range(max_prev):
        cur = cur.find_previous_sibling()
        if cur is None:
            break
        if getattr(cur, "name", None) in ("table", "figure", "h1", "h2", "h3"):
            break
        txt = normalize_text(cur.get_text(" ", strip=True)) if hasattr(cur, "get_text") else ""
        if txt:
            before_parts.append(txt)

    # next siblings
    cur = node
    for _ in range(max_next):
        cur = cur.find_next_sibling()
        if cur is None:
            break
        if getattr(cur, "name", None) in ("table", "figure", "h1", "h2", "h3"):
            break
        txt = normalize_text(cur.get_text(" ", strip=True)) if hasattr(cur, "get_text") else ""
        if txt:
            after_parts.append(txt)

    before = " ".join(reversed(before_parts))
    after = " ".join(after_parts)
    return before, after


def extract_headers_and_rows(table_elem: Tag) -> Tuple[List[str], List[List[str]]]:
    """
    Extrai headers e rows de forma robusta.
    - Se houver thead, usa th.
    - Senão, tenta usar primeira linha com th como header.
    - Rows incluem td e th (quando th aparece como primeira célula da linha).
    """
    headers: List[str] = []
    rows: List[List[str]] = []

    # 1) headers via thead
    thead = table_elem.find("thead")
    if thead:
        ths = thead.find_all("th")
        headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths if normalize_text(th.get_text(" ", strip=True))]
    else:
        # 2) first tr with ths
        first_tr = table_elem.find("tr")
        if first_tr:
            ths = first_tr.find_all("th")
            if ths:
                headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths]

    # 3) rows via tbody if exists, else all trs excluding header row
    tbody = table_elem.find("tbody")
    trs = tbody.find_all("tr") if tbody else table_elem.find_all("tr")

    for tr in trs:
        # skip header-like tr if it matches headers extracted
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row = [normalize_text(c.get_text(" ", strip=True)) for c in cells]
        # if this row is identical to headers, skip as data
        if headers and len(row) == len(headers) and all(normalize_key(a) == normalize_key(b) for a, b in zip(row, headers)):
            continue
        # remove empty trailing cells
        while row and row[-1] == "":
            row.pop()
        if row:
            rows.append(row)

    # If no headers but rows exist, create generic headers
    if not headers and rows:
        headers = [f"col_{i+1}" for i in range(len(rows[0]))]

    return headers, rows


def extract_tables_from_html(html: str) -> List[Dict]:
    tables = []
    soup = BeautifulSoup(html, "html.parser")

    for idx, table_elem in enumerate(soup.find_all("table"), 1):
        caption_tag = table_elem.find("caption")
        caption = normalize_text(caption_tag.get_text(" ", strip=True)) if caption_tag else ""
        table_name = caption if caption else f"Tabela {idx}"

        headers, rows = extract_headers_and_rows(table_elem)
        before_txt, after_txt = collect_neighbor_text(table_elem)
        context = " ".join([before_txt, caption, after_txt]).strip()

        # structural hash (for duplication detection)
        norm_html = normalize_text(re.sub(r"\s+", " ", str(table_elem)))
        t_hash = hashlib.md5(norm_html.encode("utf-8")).hexdigest()

        tables.append({
            "numero": idx,
            "nome": table_name,
            "headers": headers,
            "rows_raw": rows,
            "html": str(table_elem),
            "context_before": before_txt,
            "context_after": after_txt,
            "context_all": context,
            "hash": t_hash,
        })
    return tables


def extract_figures_from_html(html: str) -> List[Dict]:
    """
    Captura <figure> e <img> com possíveis legendas e fontes.
    """
    soup = BeautifulSoup(html, "html.parser")
    figures: List[Dict] = []

    # Prefer <figure>
    for i, fig in enumerate(soup.find_all("figure"), 1):
        cap = fig.find("figcaption")
        caption = normalize_text(cap.get_text(" ", strip=True)) if cap else ""
        img = fig.find("img")
        alt = normalize_text(img.get("alt", "")) if img else ""
        before_txt, after_txt = collect_neighbor_text(fig)
        context = " ".join([before_txt, caption, alt, after_txt]).strip()

        norm_html = normalize_text(re.sub(r"\s+", " ", str(fig)))
        f_hash = hashlib.md5(norm_html.encode("utf-8")).hexdigest()

        figures.append({
            "numero": i,
            "nome": caption or alt or f"Figura/Gráfico {i}",
            "caption": caption,
            "alt": alt,
            "context_before": before_txt,
            "context_after": after_txt,
            "context_all": context,
            "hash": f_hash,
            "html": str(fig),
        })

    # Any standalone <img> outside <figure> (optional, conservative)
    # You can enable if needed; keeping conservative to avoid noise.
    return figures


# =========================
# Rule helpers
# =========================
def make_issue(severity: str, kind: str, ident: str, rule: str, issue: str, detail: str, rec: str) -> Dict:
    return {
        "severity": severity,
        "kind": kind,          # "Tabela" / "Figura" / "Documento"
        "table": ident,        # keep key name for frontend compatibility
        "rule": rule,
        "issue": issue,
        "detail": detail,
        "recommendation": rec
    }

def find_source_text(context: str) -> Optional[str]:
    """
    Procura por "Fonte" com variações.
    """
    if not context:
        return None
    # normalize but keep original snippet
    m = re.search(r"\bFonte\b\s*[:\-–—]\s*.+?(?=$|\bNota\b|\bObserva|\bObs\b)", context, flags=re.IGNORECASE)
    if m:
        return normalize_text(m.group(0))
    # sometimes just "Fonte: XYZ" at end
    m2 = re.search(r"\bFonte\b\s*[:\-–—]\s*.+$", context, flags=re.IGNORECASE)
    if m2:
        return normalize_text(m2.group(0))
    return None

def detect_thousand_inconsistency(values: List[str]) -> bool:
    """
    Detecta mistura de formatos:
    - "1290" e "1.290"
    - "8.415" e "8415" etc.
    """
    saw_plain_4plus = False
    saw_thousand_dot = False
    for v in values:
        t = normalize_text(v)
        if re.match(r"^\d{4,}$", t):  # 4+ digits without dot
            saw_plain_4plus = True
        if THOUSAND_DOT_RE.match(t):
            saw_thousand_dot = True
    return saw_plain_4plus and saw_thousand_dot

def is_time_series_table(table: Dict) -> Tuple[bool, str]:
    """
    True if:
    - >=2 year columns in headers (20XX)
    OR
    - first column values look like years in many rows
    OR
    - caption/context indicates range 20xx a 20xx
    """
    headers = table.get("headers", [])
    rows = table.get("rows_raw", [])
    name = table.get("nome", "")
    ctx = table.get("context_all", "")

    year_cols = [i for i, h in enumerate(headers) if re.match(r"^20\d{2}$", normalize_text(h))]
    if len(year_cols) >= 2:
        return True, "year_columns"

    rng = detect_year_range(name) or detect_year_range(ctx)
    if rng:
        return True, "range_in_title"

    # first column year-like (Ano down rows)
    if rows:
        first_col = [normalize_text(r[0]) for r in rows if r and normalize_text(r[0])]
        year_like = sum(1 for x in first_col if re.match(r"^20\d{2}$", x))
        if year_like >= max(2, int(0.6 * len(first_col))):
            return True, "year_in_first_column"

    return False, ""


# =========================
# Rules: document-level
# =========================
def rule_broken_chars_in_text(html: str) -> List[Dict]:
    issues = []
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    for ch in BROKEN_CHARS:
        if ch in text:
            issues.append(make_issue(
                "WARN", "Documento", "Documento", "encoding_error",
                "Caractere inválido detectado",
                f"Encontrado caractere problemático '{ch}' no texto renderizado.",
                "Verificar encoding/geração do conteúdo (substituir caractere quebrado)."
            ))
    return issues


# =========================
# Rules: table-level
# =========================
def rule_table_empty(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return make_issue("FAIL", "Tabela", table["nome"], "table_empty",
                          "Tabela vazia", "Sem linhas de dados.", "Inserir dados ou remover a tabela.")
    # numeric scan
    nums = []
    for r in rows:
        for c in r:
            n = parse_number_ptbr(c)
            if n is not None:
                nums.append(n)
    if nums and all(abs(n) < 1e-12 for n in nums):
        return make_issue("FAIL", "Tabela", table["nome"], "table_all_zero",
                          "Tabela integralmente zerada", "Todos os valores numéricos são 0.", "Verificar extração/consulta e atualizar a tabela.")
    # if no numeric at all, not 'empty' here (handled elsewhere)
    return None


def rule_table_without_data(table: Dict) -> Optional[Dict]:
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    has_numeric = False
    for r in rows:
        for c in r:
            if parse_number_ptbr(c) is not None:
                has_numeric = True
                break
        if has_numeric:
            break
    if not has_numeric:
        return make_issue("FAIL", "Tabela", table["nome"], "table_without_data",
                          "Tabela sem quantitativos", "Não foram detectados valores numéricos.", "Inserir quantitativos ou revisar o HTML/extração.")
    return None


def rule_blank_cells(table: Dict) -> List[Dict]:
    issues = []
    rows = table.get("rows_raw", [])
    if not rows:
        return issues
    blanks = 0
    nds = 0
    samples = []
    for i, r in enumerate(rows, 1):
        for j, c in enumerate(r, 1):
            if is_empty_cell(c):
                blanks += 1
                if len(samples) < 6:
                    samples.append(f"linha {i}, col {j}")
            elif is_nd_cell(c):
                nds += 1
                if len(samples) < 6:
                    samples.append(f"ND em linha {i}, col {j}")

    if blanks > 0:
        issues.append(make_issue(
            "WARN", "Tabela", table["nome"], "blank_cells",
            "Células em branco detectadas",
            f"{blanks} célula(s) vazia(s). Amostra: {', '.join(samples)}",
            "Preencher, justificar com nota, ou padronizar marcador de ausência."
        ))

    # ND without explanation (need note)
    if nds > 0:
        src = table.get("context_all", "")
        has_note = re.search(r"\bND\b\s*[:\-–—]\s*(dado|dados).*", src, flags=re.IGNORECASE) is not None
        sev = "WARN" if has_note else "FAIL"
        issues.append(make_issue(
            sev, "Tabela", table["nome"], "nd_cells",
            "Células com ND detectadas",
            f"{nds} ocorrência(s) de ND/n.d. {'com' if has_note else 'sem'} nota explicativa próxima.",
            "Adicionar nota explicando ND (ex.: 'ND: dado não disponível') ou corrigir preenchimento."
        ))
    return issues


def rule_source_required(table: Dict) -> Optional[Dict]:
    ctx = table.get("context_all", "")
    src = find_source_text(ctx)
    if not src:
        return make_issue("FAIL", "Tabela", table["nome"], "table_source_required",
                          "Fonte não identificada", "Não foi encontrado trecho 'Fonte:' no entorno da tabela.", "Adicionar 'Fonte: ...' junto à tabela.")
    return None


def rule_source_style_inconsistency(table: Dict) -> Optional[Dict]:
    """
    Sinaliza inconsistência leve na grafia de "Fonte:" (ex.: 'Fonte :' ou 'FONTES').
    """
    ctx = table.get("context_all", "")
    if not ctx:
        return None
    # found Fonte but weird formatting
    if re.search(r"\bFONTES\b|\bFonte\s{2,}:", ctx):
        return make_issue("WARN", "Tabela", table["nome"], "source_format_inconsistency",
                          "Padronização inconsistente de fonte", "Há variação incomum na escrita de 'Fonte' no entorno da tabela.", "Padronizar para 'Fonte: <origem>'.")
    return None


def rule_numeric_format_inconsistency(table: Dict) -> Optional[Dict]:
    """
    Detecta mistura de separador de milhar (1290 vs 1.290) e decimal com ponto.
    """
    rows = table.get("rows_raw", [])
    if not rows:
        return None
    all_cells = [c for r in rows for c in r if isinstance(c, str)]
    if detect_thousand_inconsistency(all_cells):
        return make_issue(
            "WARN", "Tabela", table["nome"], "thousand_separator_inconsistency",
            "Separador de milhar inconsistente",
            "Há números com e sem separador de milhar (ex.: 1290 vs 1.290).",
            "Padronizar separador de milhar com ponto (pt-BR)."
        )
    # decimal dot in text
    if any(DECIMAL_DOT_RE.search(normalize_text(c)) for c in all_cells):
        # avoid false positives where it's thousand-dot like 1.234
        suspicious = []
        for c in all_cells:
            t = normalize_text(c)
            if re.match(r"^\d+\.\d+$", t) and not THOUSAND_DOT_RE.match(t):
                suspicious.append(t)
        if suspicious:
            return make_issue(
                "WARN", "Tabela", table["nome"], "decimal_dot",
                "Decimal com ponto detectado",
                f"Encontrado(s) decimal(is) com ponto (ex.: {', '.join(suspicious[:5])}).",
                "Padronizar decimal com vírgula (pt-BR)."
            )
    return None


def _row_label(row: List[str]) -> str:
    return normalize_text(row[0]) if row else ""

def rule_duplicate_rows_strict(table: Dict) -> Optional[Dict]:
    """
    Só sinaliza duplicidade quando:
    - label (normalizado) é igual e valores iguais
    Não sinaliza quando apenas valores coincidem em labels diferentes.
    """
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 2:
        return None

    seen = {}
    for i, r in enumerate(rows, 1):
        if not r:
            continue
        label = normalize_key(_row_label(r))
        values = tuple(normalize_text(x) for x in r[1:])  # keep raw for evidence
        sig = (label,) + values
        if label and sig in seen:
            j = seen[sig]
            return make_issue(
                "WARN", "Tabela", table["nome"], "duplicated_row",
                "Linha duplicada detectada",
                f"Linhas {j} e {i} repetem o mesmo rótulo e os mesmos valores ('{normalize_text(r[0])}').",
                "Verificar duplicidade de cadastro ou repetição indevida na tabela."
            )
        seen[sig] = i
    return None


def find_total_row_index(rows: List[List[str]]) -> Optional[int]:
    for idx, r in enumerate(rows):
        if not r:
            continue
        first = normalize_key(r[0])
        if first in TOTAL_WORDS:
            return idx
    return None

def find_total_col_index(headers: List[str]) -> Optional[int]:
    for i, h in enumerate(headers):
        if normalize_key(h) in TOTAL_WORDS or normalize_key(h).endswith(" total"):
            return i
    return None

def rule_totals(table: Dict) -> List[Dict]:
    """
    Valida soma se houver Total explícito (linha ou coluna).
    Conservadora: só soma colunas/linhas numéricas evidentes.
    """
    issues = []
    headers = table.get("headers", [])
    rows = table.get("rows_raw", [])
    if not headers or not rows:
        return issues

    total_row = find_total_row_index(rows)
    total_col = find_total_col_index(headers)

    # If no explicit total, skip
    if total_row is None and total_col is None:
        return issues

    # identify numeric columns (excluding label col 0)
    def col_numeric_ratio(col_idx: int) -> float:
        vals = []
        for r in rows:
            if col_idx < len(r):
                n = parse_number_ptbr(r[col_idx])
                if n is not None:
                    vals.append(n)
        if not vals:
            return 0.0
        return len(vals) / max(1, len(rows))

    numeric_cols = [i for i in range(1, len(headers)) if col_numeric_ratio(i) >= 0.5]

    # 1) check total row: sum of rows above per numeric col
    if total_row is not None:
        for c in numeric_cols:
            if c >= len(headers):
                continue
            reported = parse_number_ptbr(rows[total_row][c]) if c < len(rows[total_row]) else None
            if reported is None:
                continue
            calc = 0.0
            used = 0
            for r_i in range(0, total_row):
                if c < len(rows[r_i]):
                    n = parse_number_ptbr(rows[r_i][c])
                    if n is not None:
                        calc += n
                        used += 1
            # compare only if used some values
            if used >= 1 and abs(calc - reported) > 0.5:
                issues.append(make_issue(
                    "FAIL", "Tabela", table["nome"], "total_row_mismatch",
                    "Divergência em total (linha Total)",
                    f"Coluna '{headers[c]}': soma calculada={calc:.0f} vs total informado={reported:.0f}.",
                    "Recalcular e corrigir total ou revisar critérios de somatório."
                ))

    # 2) check total column: row-wise sum across numeric cols excluding total col
    if total_col is not None:
        for r_i, r in enumerate(rows):
            if not r or (r_i == total_row):
                continue
            label = normalize_key(r[0])
            if label in TOTAL_WORDS:
                continue
            if total_col >= len(r):
                continue
            reported = parse_number_ptbr(r[total_col])
            if reported is None:
                continue
            calc = 0.0
            used = 0
            for c in numeric_cols:
                if c == total_col:
                    continue
                if c < len(r):
                    n = parse_number_ptbr(r[c])
                    if n is not None:
                        calc += n
                        used += 1
            if used >= 2 and abs(calc - reported) > 0.5:
                issues.append(make_issue(
                    "FAIL", "Tabela", table["nome"], "total_col_mismatch",
                    "Divergência em total (coluna Total)",
                    f"Linha '{normalize_text(r[0])}': soma calculada={calc:.0f} vs total informado={reported:.0f}.",
                    "Recalcular e corrigir total ou revisar critérios de somatório."
                ))

    return issues


def rule_year_base_mentions(table: Dict, base_year: int) -> List[Dict]:
    """
    Detecta anos fora do ano-base em títulos/caption/context.
    Regras:
    - Se não for série temporal e mencionar ano != base_year => WARN/FAIL dependendo do caso.
    - Se for série temporal, o intervalo deve incluir base_year (se houver range explícito).
    """
    issues = []
    name = table.get("nome", "")
    ctx = table.get("context_all", "")

    is_ts, ts_reason = is_time_series_table(table)

    years = set(detect_years_in_text(name + " " + ctx))
    if not years:
        return issues

    # If explicit range present, validate includes base_year
    rng = detect_year_range(name) or detect_year_range(ctx)
    if rng and is_ts:
        a, b = rng
        if not (min(a, b) <= base_year <= max(a, b)):
            issues.append(make_issue(
                "FAIL", "Tabela", name, "year_range_mismatch",
                "Série histórica não inclui o ano-base",
                f"Intervalo detectado {a} a {b}, mas ano-base é {base_year}.",
                "Atualizar intervalo para incluir o ano-base ou corrigir o título."
            ))
        return issues  # range handled

    # If not time series: any year != base_year is suspicious
    if not is_ts:
        other_years = sorted([y for y in years if y != base_year])
        if other_years:
            issues.append(make_issue(
                "WARN", "Tabela", name, "year_outside_base",
                "Ano diferente do ano-base detectado",
                f"Foram encontrados anos {other_years} em tabela não temporal (ano-base={base_year}).",
                "Revisar título/legenda/artefato para garantir atualização para o ano-base."
            ))
    return issues


def rule_extreme_variation(table: Dict) -> List[Dict]:
    """
    Detecta quedas/aumentos expressivos em série temporal.
    Só roda se for série temporal de verdade.
    """
    issues = []
    headers = table.get("headers", [])
    rows = table.get("rows_raw", [])
    if not headers or not rows:
        return issues

    is_ts, reason = is_time_series_table(table)
    if not is_ts:
        return issues

    # Case A: year columns
    year_cols = [(i, int(headers[i])) for i in range(len(headers)) if re.match(r"^20\d{2}$", normalize_text(headers[i]))]
    if len(year_cols) >= 2:
        year_cols.sort(key=lambda x: x[1])  # by year
        for r_i, r in enumerate(rows, 1):
            if not r:
                continue
            label = normalize_text(r[0]) if r else f"Linha {r_i}"
            # gather values
            seq = []
            for col_idx, year in year_cols:
                if col_idx < len(r):
                    n = parse_number_ptbr(r[col_idx])
                    if n is not None:
                        seq.append((year, n))
            if len(seq) < 2:
                continue
            # compare consecutive
            for k in range(len(seq) - 1):
                y0, v0 = seq[k]
                y1, v1 = seq[k + 1]
                if v0 == 0:
                    # avoid inf; but if jump from 0 to big, warn
                    if v1 >= 100:
                        issues.append(make_issue(
                            "WARN", "Tabela", table["nome"], "jump_from_zero",
                            "Aumento expressivo a partir de zero",
                            f"Categoria '{label}': {y0}={v0:.0f} → {y1}={v1:.0f}.",
                            "Validar se houve mudança metodológica ou erro de registro."
                        ))
                    continue
                pct = ((v1 - v0) / v0) * 100.0
                apct = abs(pct)
                if apct > 500:
                    issues.append(make_issue(
                        "FAIL", "Tabela", table["nome"], "extreme_year_variation",
                        "Variação extrema > 500%",
                        f"Categoria '{label}': {y0}={v0:.0f} → {y1}={v1:.0f} ({pct:+.1f}%).",
                        "Verificar integridade dos dados (possível erro de digitação/extração)."
                    ))
                elif apct > 300:
                    issues.append(make_issue(
                        "WARN", "Tabela", table["nome"], "extreme_year_variation",
                        "Variação extrema 300–500%",
                        f"Categoria '{label}': {y0}={v0:.0f} → {y1}={v1:.0f} ({pct:+.1f}%).",
                        "Validar dados com a fonte."
                    ))
                elif apct > 40:
                    issues.append(make_issue(
                        "WARN", "Tabela", table["nome"], "abrupt_change",
                        "Variação abrupta > 40%",
                        f"Categoria '{label}': {y0}={v0:.0f} → {y1}={v1:.0f} ({pct:+.1f}%).",
                        "Inserir nota explicativa se for variação esperada."
                    ))
        return issues

    # Case B: year in first column (Ano down rows)
    # rows: [Ano, metric1, metric2...]
    first_col_yearlike = sum(1 for r in rows if r and re.match(r"^20\d{2}$", normalize_text(r[0])))
    if first_col_yearlike >= 2 and len(headers) >= 2:
        # For each metric column, compare year-to-year down rows
        # Build list of (year, row)
        yrows = []
        for r in rows:
            if r and re.match(r"^20\d{2}$", normalize_text(r[0])):
                yrows.append((int(normalize_text(r[0])), r))
        yrows.sort(key=lambda x: x[0])
        for col_idx in range(1, min(len(headers), max(len(r) for _, r in yrows))):
            series = []
            for year, r in yrows:
                if col_idx < len(r):
                    n = parse_number_ptbr(r[col_idx])
                    if n is not None:
                        series.append((year, n))
            if len(series) < 2:
                continue
            metric_name = headers[col_idx] if col_idx < len(headers) else f"col_{col_idx+1}"
            for k in range(len(series) - 1):
                y0, v0 = series[k]
                y1, v1 = series[k + 1]
                if v0 == 0:
                    if v1 >= 100:
                        issues.append(make_issue(
                            "WARN", "Tabela", table["nome"], "jump_from_zero",
                            "Aumento expressivo a partir de zero",
                            f"Métrica '{metric_name}': {y0}={v0:.0f} → {y1}={v1:.0f}.",
                            "Validar se houve mudança metodológica ou erro."
                        ))
                    continue
                pct = ((v1 - v0) / v0) * 100.0
                apct = abs(pct)
                if apct > 500:
                    issues.append(make_issue(
                        "FAIL", "Tabela", table["nome"], "extreme_year_variation",
                        "Variação extrema > 500%",
                        f"Métrica '{metric_name}': {y0}={v0:.0f} → {y1}={v1:.0f} ({pct:+.1f}%).",
                        "Verificar integridade dos dados."
                    ))
                elif apct > 300:
                    issues.append(make_issue(
                        "WARN", "Tabela", table["nome"], "extreme_year_variation",
                        "Variação extrema 300–500%",
                        f"Métrica '{metric_name}': {y0}={v0:.0f} → {y1}={v1:.0f} ({pct:+.1f}%).",
                        "Validar dados com a fonte."
                    ))
                elif apct > 40:
                    issues.append(make_issue(
                        "WARN", "Tabela", table["nome"], "abrupt_change",
                        "Variação abrupta > 40%",
                        f"Métrica '{metric_name}': {y0}={v0:.0f} → {y1}={v1:.0f} ({pct:+.1f}%).",
                        "Inserir nota explicativa se necessário."
                    ))
    return issues


def rule_siglas_nomenclature(table: Dict) -> List[Dict]:
    """
    - Sigla repetida com nomes diferentes
    - Nome repetido com siglas diferentes
    - Abreviações irregulares
    - Caixa alta inconsistente (heurística leve)
    """
    issues = []
    rows = table.get("rows_raw", [])
    if not rows:
        return issues

    sigla_to_names: Dict[str, set] = {}
    name_to_siglas: Dict[str, set] = {}

    # gather labels (first column)
    labels = [normalize_text(r[0]) for r in rows if r and normalize_text(r[0])]
    # abbreviations / case checks
    abbr_hits = []
    upper_hits = 0

    for lab in labels:
        # broken encoding inside label
        for ch in BROKEN_CHARS:
            if ch in lab:
                issues.append(make_issue(
                    "WARN", "Tabela", table["nome"], "encoding_error",
                    "Caractere inválido em rótulo",
                    f"Rótulo contém caractere '{ch}': '{lab}'",
                    "Corrigir encoding/geração do texto."
                ))
                break

        if re.search(r"\bDepto\b|\bDep\.\b|\bEstut\b", lab):
            abbr_hits.append(lab)

        if lab.isupper() and len(lab) >= 8 and not SIGLA_RE.fullmatch(lab):
            upper_hits += 1

        m = SIGLA_NAME_RE.match(lab)
        if m:
            sigla = m.group(1)
            nm = normalize_text(m.group(2))
            sigla_to_names.setdefault(sigla, set()).add(normalize_key(nm))
            name_to_siglas.setdefault(normalize_key(nm), set()).add(sigla)

    # report inconsistencies
    for sigla, names in sigla_to_names.items():
        if len(names) >= 2:
            issues.append(make_issue(
                "WARN", "Tabela", table["nome"], "sigla_multiple_names",
                "Sigla associada a nomes diferentes",
                f"Sigla '{sigla}' aparece associada a mais de um nome (variações detectadas).",
                "Padronizar nomenclatura institucional para a sigla."
            ))

    for nm, siglas in name_to_siglas.items():
        if len(siglas) >= 2:
            issues.append(make_issue(
                "WARN", "Tabela", table["nome"], "name_multiple_siglas",
                "Nome associado a siglas diferentes",
                f"Mesmo nome (normalizado) aparece com siglas distintas: {sorted(siglas)}.",
                "Padronizar sigla oficial da unidade."
            ))

    if abbr_hits:
        issues.append(make_issue(
            "WARN", "Tabela", table["nome"], "irregular_abbreviations",
            "Uso irregular de abreviações",
            f"Foram encontradas abreviações potencialmente irregulares (ex.: {normalize_text(abbr_hits[0])}).",
            "Padronizar nomes (preferir denominação completa ou padrão institucional)."
        ))

    if upper_hits >= max(2, int(0.3 * len(labels))):
        issues.append(make_issue(
            "WARN", "Tabela", table["nome"], "inconsistent_uppercase",
            "Uso inconsistente de caixa alta",
            "Há quantidade significativa de rótulos em CAIXA ALTA total, divergindo do padrão.",
            "Uniformizar capitalização conforme padrão do anuário."
        ))

    return issues


def rule_spelling_typos(table: Dict) -> List[Dict]:
    """
    Heurísticas leves de erro/digitação:
    - palavras suspeitas conhecidas
    - palavras longas sem vogais
    - palavras coladas com pontuação estranha
    """
    issues = []
    rows = table.get("rows_raw", [])
    if not rows:
        return issues

    sample = []
    for r in rows:
        for c in r:
            t = normalize_text(c)
            if not t:
                continue
            low = normalize_key(t)

            # suspicious known substrings
            if any(sw in low for sw in SUSPECT_WORDS):
                sample.append(t)

            # long "no vowels" tokens
            for tok in re.findall(r"\b[\wÀ-ÿ]{7,}\b", t):
                tl = normalize_key(tok)
                if tl.isdigit():
                    continue
                if not re.search(r"[aeiou]", tl):
                    sample.append(tok)

            # glued abbreviations
            if re.search(r"\w+\.\w+", t):
                sample.append(t)

    if sample:
        issues.append(make_issue(
            "WARN", "Tabela", table["nome"], "possible_typos",
            "Possíveis erros de digitação/português",
            f"Exemplos detectados: {', '.join(list(dict.fromkeys(sample))[:4])}",
            "Revisar grafia/nomenclatura e corrigir abreviações inconsistentes."
        ))
    return issues


def rule_structural_duplicate_tables(tables: List[Dict]) -> List[Dict]:
    issues = []
    seen = {}
    for t in tables:
        h = t.get("hash")
        if not h:
            continue
        if h in seen:
            issues.append(make_issue(
                "WARN", "Documento", "Documento", "duplicate_table_structure",
                "Duplicidade estrutural de tabela",
                f"A tabela '{t['nome']}' parece duplicada de '{seen[h]}' (mesma estrutura).",
                "Verificar se houve repetição indevida de tabela na página."
            ))
        else:
            seen[h] = t["nome"]
    return issues


# =========================
# Rules: figures/graphs
# =========================
def rule_figure_source_required(fig: Dict) -> Optional[Dict]:
    src = find_source_text(fig.get("context_all", ""))
    if not src:
        return make_issue("FAIL", "Figura", fig["nome"], "figure_source_required",
                          "Fonte não identificada (figura/gráfico)",
                          "Não foi encontrado trecho 'Fonte:' no entorno da figura/gráfico.",
                          "Adicionar 'Fonte: ...' junto à figura/gráfico.")
    return None

def rule_figure_year_base(fig: Dict, base_year: int) -> Optional[Dict]:
    ctx = fig.get("context_all", "")
    years = set(detect_years_in_text(ctx))
    if not years:
        return None
    # If mentions year and it's not a time range including base_year, warn
    rng = detect_year_range(ctx)
    if rng:
        a, b = rng
        if not (min(a, b) <= base_year <= max(a, b)):
            return make_issue("FAIL", "Figura", fig["nome"], "figure_year_range_mismatch",
                              "Intervalo de anos não inclui o ano-base",
                              f"Intervalo {a} a {b} detectado, mas ano-base é {base_year}.",
                              "Atualizar arte/legenda para incluir o ano-base.")
        return None
    other = [y for y in years if y != base_year]
    if other:
        return make_issue("WARN", "Figura", fig["nome"], "figure_year_outside_base",
                          "Ano diferente do ano-base em figura/gráfico",
                          f"Foram encontrados anos {sorted(other)} no entorno (ano-base={base_year}).",
                          "Revisar título/legenda/arte e regenerar se necessário.")
    return None


# =========================
# Analysis orchestrator
# =========================
def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues: List[Dict] = []

    # Hard failures first
    x = rule_table_empty(table)
    if x:
        issues.append(x)
        # still continue to report source/year if possible (helpful)
    x = rule_table_without_data(table)
    if x:
        issues.append(x)

    # Content + formatting
    issues.extend(rule_blank_cells(table))
    s = rule_source_required(table)
    if s:
        issues.append(s)
    s2 = rule_source_style_inconsistency(table)
    if s2:
        issues.append(s2)

    issues.extend(rule_totals(table))
    yissues = rule_year_base_mentions(table, base_year)
    issues.extend(yissues)

    nf = rule_numeric_format_inconsistency(table)
    if nf:
        issues.append(nf)

    dr = rule_duplicate_rows_strict(table)
    if dr:
        issues.append(dr)

    issues.extend(rule_extreme_variation(table))
    issues.extend(rule_siglas_nomenclature(table))
    issues.extend(rule_spelling_typos(table))

    return issues


def run_audit(url: str, report_year: int, base_year: int) -> List[Dict]:
    issues: List[Dict] = []
    html, diag = download_page(url)

    if not html:
        issues.append(make_issue("FAIL", "Documento", "Documento", "download_failed",
                                 "Falha ao baixar página", diag.get("status", ""), "Verificar URL/conectividade."))
        return issues

    if diag["contagem_tables"] == 0:
        issues.append(make_issue("FAIL", "Documento", "Documento", "no_tables",
                                 "Nenhuma tabela encontrada", "A página não contém <table> no HTML baixado.", "Verificar se a URL está correta ou se há renderização dinâmica."))
    else:
        issues.append(make_issue("PASS", "Documento", "Documento", "document_ok",
                                 f"✓ {diag['contagem_tables']} tabela(s) encontrada(s)",
                                 f"HTML: {diag['tamanho_html_kb']:.1f} KB | Figuras: {diag.get('contagem_figuras', 0)}",
                                 "Analisando conteúdo..."))

    # Document-level checks
    issues.extend(rule_broken_chars_in_text(html))

    tables = extract_tables_from_html(html)
    issues.extend(rule_structural_duplicate_tables(tables))

    for t in tables:
        issues.extend(analyze_table(t, base_year))

    # Figures
    figs = extract_figures_from_html(html)
    for f in figs:
        fx = rule_figure_source_required(f)
        if fx:
            issues.append(fx)
        fy = rule_figure_year_base(f, base_year)
        if fy:
            issues.append(fy)

    return issues


# =========================
# Report generator
# =========================
def generate_txt_report(issues: List[Dict], url: str, report_year: int, base_year: int) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    txt = "=" * 88 + "\nAUDITORIA DO ANUÁRIO ESTATÍSTICO UnB\n" + "=" * 88 + "\n\n"
    txt += f"Data: {now}\nURL: {url}\nAno do relatório: {report_year}\nAno-base: {base_year}\n\n"

    fail = [i for i in issues if i["severity"] == "FAIL"]
    warn = [i for i in issues if i["severity"] == "WARN"]
    pas = [i for i in issues if i["severity"] == "PASS"]

    txt += f"FAIL: {len(fail)} | WARN: {len(warn)} | PASS: {len(pas)}\n\n"

    def block(items: List[Dict], title: str):
        nonlocal txt
        if not items:
            return
        txt += title + "\n" + "-" * 60 + "\n"
        for k, it in enumerate(items, 1):
            kind = it.get("kind", "")
            ident = it.get("table", "")
            txt += f"{k}. [{it['severity']}] {it['issue']}\n"
            txt += f"   Tipo: {kind} | Item: {ident} | Regra: {it['rule']}\n"
            txt += f"   Detalhe: {it['detail']}\n"
            txt += f"   💡 {it['recommendation']}\n\n"

    block(fail, "ERROS (FAIL):")
    block(warn, "AVISOS (WARN):")
    # PASS usually just 1 line; keep concise
    if pas:
        txt += "STATUS (PASS):\n" + "-" * 60 + "\n"
        for it in pas:
            txt += f"- {it['issue']} | {it['detail']}\n"
        txt += "\n"

    return txt


# =========================
# UI (simple)
# =========================
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
    .issue { padding: 15px; margin: 10px 0; border-left: 5px solid; border-radius: 6px; }
    .fail { background: #ffebee; border-color: #2E1D86; }
    .warn { background: #fff7d6; border-color: #FDCA00; }
    .pass { background: #e8f5e9; border-color: #006633; }
    small { color: #555; }
  </style>
</head>
<body>
  <div class="container">
    <h1>📋 Auditoria - Anuário UnB</h1>
    <input type="url" id="url" value="https://anuariounb2025.netlify.app/" placeholder="URL">
    <input type="number" id="year" value="2025" placeholder="Ano do relatório">
    <input type="number" id="base" value="2024" placeholder="Ano-base">
    <button onclick="audit()">🔍 Executar</button>

    <div id="results" class="results">
      <div id="content"></div>
      <button onclick="downloadReport()" style="background:#006633;">📥 Baixar TXT</button>
    </div>
  </div>

  <script>
    let lastIssues = [], lastUrl = '', lastYear = 2025, lastBase = 2024;

    async function audit() {
      const url = document.getElementById('url').value;
      const year = parseInt(document.getElementById('year').value);
      const base = parseInt(document.getElementById('base').value);
      lastUrl = url; lastYear = year; lastBase = base;

      try {
        const res = await fetch('/audit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, report_year: year, base_year: base })
        });
        const data = await res.json();
        lastIssues = data.issues || [];

        const html = lastIssues.map(i => `
          <div class="issue ${i.severity.toLowerCase()}">
            <strong>[${i.severity}] ${i.issue}</strong><br>
            <small>${i.kind} | ${i.table} | ${i.rule}</small><br>
            <small>${i.detail}</small><br>
            💡 ${i.recommendation}
          </div>
        `).join('');

        document.getElementById('content').innerHTML = html || '<em>Nenhum achado.</em>';
        document.getElementById('results').style.display = 'block';
      } catch (e) {
        alert('Erro: ' + e.message);
      }
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
        a.click();
      });
    }
  </script>
</body>
</html>
"""

# =========================
# Endpoints
# =========================
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
    issues = data.get("issues", [])
    url = data.get("url", "")
    report_year = int(data.get("report_year", 2025))
    base_year = int(data.get("base_year", 2024))
    txt = generate_txt_report(issues, url, report_year, base_year)
    return StreamingResponse(
        iter([txt.encode("utf-8")]),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=auditoria-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"}
    )

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
