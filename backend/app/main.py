import re
from typing import List, Dict, Tuple, Optional, Any
from bs4 import BeautifulSoup

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

    # Remove marcadores comuns (ex.: notas)
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

    # 1234,56 (sem milhar)
    if re.match(r'^\d+,\d+$', s):
        try:
            return float(s.replace(',', '.'))
        except:
            return None

    # 1234.56 (padrão EN - geralmente indesejado)
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


# =========================
# Extração robusta de tabelas
# =========================

def extract_tables_from_html(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    tables = []

    all_tables = soup.find_all("table")
    for table_idx, table_elem in enumerate(all_tables, 1):
        # Nome: caption ou aria-label ou id
        caption = table_elem.find("caption")
        table_name = normalize_text(caption.get_text(" ", strip=True)) if caption else ""
        if not table_name:
            table_name = normalize_text(table_elem.get("aria-label", "")) or f"Tabela {table_idx}"

        # Headers
        headers = []
        thead = table_elem.find("thead")
        if thead:
            headers = [normalize_text(th.get_text(" ", strip=True)) for th in thead.find_all("th")]
        else:
            # fallback: primeira linha com <th>
            first_tr = table_elem.find("tr")
            if first_tr:
                ths = first_tr.find_all("th")
                if ths:
                    headers = [normalize_text(th.get_text(" ", strip=True)) for th in ths]

        # Rows
        rows_raw = []
        tbody = table_elem.find("tbody") or table_elem
        for tr in tbody.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            cells = [normalize_text(td.get_text(" ", strip=True)) for td in tds]
            # descarta linha vazia
            if any(c != "" for c in cells):
                rows_raw.append(cells)

        # Contexto ao redor (ajuda a detectar Fonte fora da tabela)
        around_text = []
        # pega próximos irmãos (até 5) depois da tabela
        sib = table_elem
        for _ in range(5):
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
            "around_text": around_text,   # <- importante
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
        'série', 'evolução', 'históric', 'trend', 'período', 'temporal',
        r'20\d{2}\s+a\s+20\d{2}', r'20\d{2}\s*-\s*20\d{2}'
    ]
    return any(re.search(p, t) for p in indicators)

def find_source_text(table: Dict) -> Optional[str]:
    """
    Encontra 'Fonte:' dentro do HTML da tabela OU no texto logo abaixo (around_text),
    incluindo casos em itálico (<i>/<em>).
    """
    # 1) dentro do HTML da própria tabela
    html = table.get("html", "")
    soup = BeautifulSoup(html, "html.parser")
    inside_txt = normalize_text(soup.get_text(" ", strip=True))
    m = re.search(r'\bFonte\s*:\s*(.+)$', inside_txt, flags=re.IGNORECASE)
    if m:
        return normalize_text("Fonte: " + m.group(1))

    # 2) logo após a tabela (rodapé)
    for block in table.get("around_text", []):
        m2 = re.search(r'\bFonte\s*:\s*(.+)', block, flags=re.IGNORECASE)
        if m2:
            return normalize_text("Fonte: " + m2.group(1))

    return None


# =========================
# Regras novas (cobrindo o seu checklist)
# =========================

def rule_missing_source(table: Dict) -> Optional[Dict]:
    src = find_source_text(table)
    if not src:
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
    """
    Ano-base é 2024: alerta se aparecerem anos fora do esperado no título/cabeçalho/linhas.
    Não impede séries históricas (ex.: 2020–2024), mas pega casos tipo 2023 perdido no título.
    """
    name = normalize_text(table.get("nome", ""))
    headers = [normalize_text(h) for h in table.get("headers", [])]
    # Busca anos explícitos
    years_in_name = set(re.findall(r'\b(20\d{2})\b', name))
    years_in_headers = set(re.findall(r'\b(20\d{2})\b', " ".join(headers)))

    # Aceita séries que incluam base_year; problema é quando NÃO inclui base_year e aparece outro ano "solto"
    all_years = years_in_name.union(years_in_headers)
    if not all_years:
        return None

    if str(base_year) not in all_years:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "year_base_mismatch",
            "issue": "Ano-base (2024) não aparece na tabela",
            "detail": f"Anos detectados: {', '.join(sorted(all_years))}",
            "recommendation": "Revisar título/cabeçalho/legendas para refletir o ano-base 2024 (ou série histórica incluindo 2024)."
        }

    # Se tem 2023 e 2024, ok (série). Mas se o nome tiver "..., 2023" e a tabela é 2024, isso precisa de regra mais contextual.
    # Mantemos como WARN quando houver 2023 no título junto de 2024 (possível resíduo)
    if "2023" in years_in_name and str(base_year) in years_in_name and not has_time_series_indicator(name):
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "year_possible_residue",
            "issue": "Possível resíduo de ano no título",
            "detail": f"Título contém 2023 e {base_year} mas não parece série histórica.",
            "recommendation": "Confirmar se o título/legenda foi atualizado corretamente para o ano-base 2024."
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
            # Contar padrões só para células "numéricas aparentes"
            if re.match(r'^\d{1,3}(\.\d{3})+$', c):
                patterns["pt_milhar"] += 1; sample += 1
            elif re.match(r'^\d{1,3}(\.\d{3})*,\d+$', c) or re.match(r'^\d+,\d+$', c):
                patterns["pt_decimal"] += 1; sample += 1
            elif re.match(r'^\d+\.\d+$', c):
                patterns["en_decimal"] += 1; sample += 1
            elif re.match(r'^\d+$', c):
                patterns["plain_int"] += 1; sample += 1

    if sample < 5:
        return None

    # mistura perigosa: en_decimal junto com pt_decimal
    if patterns["en_decimal"] > 0 and patterns["pt_decimal"] > 0:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "separator_mixed_decimal",
            "issue": "Mistura de separadores decimais (',' e '.')",
            "detail": f"Padrões: {patterns}",
            "recommendation": "Padronizar decimais em PT-BR: usar vírgula para decimal (ex.: 1,23)."
        }

    # mistura: milhar com e sem ponto (ex.: 1.234 e 1234)
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
    """
    Procura linha de TOTAL e compara com soma das linhas acima (por coluna numérica).
    Só aplica quando há pelo menos 3 linhas e pelo menos 2 colunas numéricas.
    """
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 3:
        return None

    # detectar índice da linha TOTAL
    total_idx = None
    for i, row in enumerate(rows):
        if row and re.match(r'^(total|TOTAL)\b', normalize_text(row[0]), flags=re.IGNORECASE):
            total_idx = i
            break
    if total_idx is None:
        return None

    # converter para matriz numérica por coluna
    n_cols = max(len(r) for r in rows)
    numeric_cols = []
    for c in range(1, n_cols):  # ignora coluna 0 (rótulo)
        nums = []
        for r in rows[:total_idx]:
            if c < len(r):
                v = parse_number_ptbr(r[c])
                if v is not None:
                    nums.append(v)
        if len(nums) >= 2:
            numeric_cols.append(c)

    if len(numeric_cols) < 2:
        return None

    # comparar soma vs total registrado
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

        # tolerância: 0,5% ou 1 unidade (para evitar ruído)
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

def rule_duplicated_rows_strict(table: Dict) -> Optional[Dict]:
    """
    Só acusa duplicidade se:
    - rótulo (coluna 0) igual (normalizado) E
    - assinatura numérica igual
    """
    rows = table.get("rows_raw", [])
    if not rows or len(rows) < 2:
        return None

    seen = {}
    for idx, row in enumerate(rows):
        label = normalize_text(row[0]) if row else ""
        sig = tuple(parse_number_ptbr(cell) for cell in row[1:])  # ignora label
        key = (label.lower(), sig)
        if key in seen and label != "":
            return {
                "severity": "WARN",
                "table": table["nome"],
                "rule": "duplicated_rows_strict",
                "issue": "Linha possivelmente duplicada",
                "detail": f"Linhas {seen[key]+1} e {idx+1} com mesmo rótulo e mesmos valores.",
                "recommendation": "Verificar duplicação estrutural (cópia/colagem)."
            }
        seen[key] = idx
    return None

def rule_extreme_year_variation(table: Dict) -> Optional[Dict]:
    """
    Variação abrupta/apenas série temporal: compara ANOS na MESMA LINHA.
    """
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
        # categoria/rótulo é a primeira célula
        categoria = normalize_text(row[0]) if row else f"Linha {r_i}"
        # coletar anos em ordem de colunas
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
            y1, v1 = values[i+1]
            if v0 == 0:
                continue
            var_pct = ((v1 - v0) / v0) * 100

            # thresholds: você pode ajustar
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


# =========================
# Siglas / nomenclatura / caixa alta
# =========================

# Exemplo (cole TODAS as suas siglas aqui)
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
    """
    - aponta siglas fora da allowlist (em headers e primeira coluna)
    - aponta uso inconsistente de caixa alta em rótulos do tipo "Total" vs "TOTAL"
    - aponta abreviações irregulares simples (ex.: "Depto" vs "Departamento")
    """
    headers = [normalize_text(h) for h in table.get("headers", [])]
    rows = table.get("rows_raw", [])

    suspects = set()
    text_pool = " ".join(headers)

    # pega também primeira coluna (rótulos)
    for row in rows[:80]:
        if row:
            text_pool += " " + normalize_text(row[0])

    for tok in SIGLA_TOKEN_RE.findall(text_pool):
        if tok not in SIGLAS_ALLOWLIST:
            suspects.add(tok)

    # caixa alta inconsistente (Total/TOTAL misturado)
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
            "issue": "Siglas não padronizadas (fora da lista)",
            "detail": f"Exemplos: {', '.join(sorted(list(suspects))[:15])}",
            "recommendation": "Revisar siglas, padronizar ou adicionar à lista oficial."
        }

    if len(total_forms) >= 2:
        return {
            "severity": "WARN",
            "table": table["nome"],
            "rule": "inconsistent_uppercase",
            "issue": "Uso inconsistente de caixa alta em 'Total'",
            "detail": f"Formas encontradas: {', '.join(sorted(total_forms))}",
            "recommendation": "Padronizar (ex.: sempre 'Total' ou sempre 'TOTAL')."
        }

    return None


# =========================
# Tabelas vazias / zeradas
# =========================

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

    # considera numéricos > 0 como evidência de dado
    has_positive = False
    for row in rows:
        for cell in row[1:]:
            v = parse_number_ptbr(cell)
            if v is not None and v != 0:
                has_positive = True
                break
        if has_positive:
            break

    if not has_positive:
        return {
            "severity": "FAIL",
            "table": table["nome"],
            "rule": "table_all_zero",
            "issue": "Tabela integralmente zerada (ou sem números válidos)",
            "detail": "Não foi encontrado valor numérico diferente de zero.",
            "recommendation": "Confirmar se a tabela deveria ter dados; se sim, revisar extração/consulta."
        }

    return None


# =========================
# Pipeline de análise
# =========================

def analyze_table(table: Dict, base_year: int) -> List[Dict]:
    issues = []

    # 0) vazia / zerada (bloqueia parte das outras)
    issue = rule_table_empty_or_all_zero(table)
    if issue:
        issues.append(issue)
        # ainda vale checar fonte/ano? opcional. Eu manteria:
        src_issue = rule_missing_source(table)
        if src_issue:
            issues.append(src_issue)
        year_issue = rule_year_base_mismatch(table, base_year)
        if year_issue:
            issues.append(year_issue)
        return issues

    # 1) fonte (corrigido: pega itálico/rodapé)
    issue = rule_missing_source(table)
    if issue:
        issues.append(issue)

    # 2) ano-base
    issue = rule_year_base_mismatch(table, base_year)
    if issue:
        issues.append(issue)

    # 3) células em branco
    issue = rule_blank_cells(table)
    if issue:
        issues.append(issue)

    # 4) separadores
    issue = rule_thousand_separator_inconsistency(table)
    if issue:
        issues.append(issue)

    # 5) totais
    issue = rule_totals_divergence(table)
    if issue:
        issues.append(issue)

    # 6) variações só em série histórica (horizontal)
    issue = rule_extreme_year_variation(table)
    if issue:
        issues.append(issue)

    # 7) duplicidades (estritas)
    issue = rule_duplicated_rows_strict(table)
    if issue:
        issues.append(issue)

    # 8) siglas / padronização
    issue = rule_acronyms_and_naming(table)
    if issue:
        issues.append(issue)

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
            "detail": "Arquivo sem tabelas HTML (ou bloqueio de acesso).",
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

