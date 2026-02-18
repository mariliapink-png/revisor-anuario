import re
import pandas as pd
from typing import Dict, List, Any, Tuple
import logging

logger = logging.getLogger(__name__)


class CheckEngine:
    """Engine para rodar as 6 regras de checagem."""
    
    def __init__(self, report_year: int, base_year: int):
        self.report_year = report_year
        self.base_year = base_year
    
    def run_all_checks(self, section_data: Dict[str, Any], url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """Roda todas as checagens e retorna lista de resultados."""
        results = []
        
        # R1: Year checks
        results.extend(self.r1_year_checks(section_data["text"], url, anchor))
        
        # R2: Decimal separator
        results.extend(self.r2_decimal_separator(section_data["text"], url, anchor))
        
        # Checagens específicas de tabelas
        for table_data in section_data.get("tables", []):
            # R3: Table source required
            results.extend(self.r3_table_source_required(table_data, url, anchor))
            
            # R4: Table totals
            results.extend(self.r4_table_totals(table_data, url, anchor))
            
            # R5: Table completeness
            results.extend(self.r5_table_completeness(table_data, url, anchor))
            
            # R6: Total row style
            results.extend(self.r6_total_row_style(table_data, url, anchor))
        
        return results
    
    # ===== R1: Year Checks =====
    def r1_year_checks(self, text: str, url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """
        R1: Verificar anos
        - FAIL se encontrar "Anuário Estatístico 2024" (ano anterior)
        - FAIL se encontrar ano inválido "20234"
        - WARN se 2023 aparecer e 2024 não
        - FAIL se série truncada (ex: "2020 a 2023" quando base_year=2024)
        """
        results = []
        
        # FAIL: encontrar "Anuário Estatístico YYYY" com ano errado
        pattern_anuario = r"Anuário\s+Estatístico\s+(\d{4})"
        matches = re.finditer(pattern_anuario, text, re.IGNORECASE)
        for match in matches:
            year_str = match.group(1)
            if int(year_str) != self.report_year:
                results.append({
                    "rule": "R1_wrong_anuario_year",
                    "severity": "FAIL",
                    "message": f"Anuário deve ser {self.report_year}, encontrado {year_str}",
                    "evidence": {
                        "text_snippet": text[max(0, match.start()-50):match.end()+50],
                        "url": url,
                        "anchor": anchor,
                    }
                })
        
        # FAIL: encontrar anos inválidos (20234, etc)
        pattern_invalid = r"20\d{3,}"
        for match in re.finditer(pattern_invalid, text):
            year_str = match.group(0)
            results.append({
                "rule": "R1_invalid_year_format",
                "severity": "FAIL",
                "message": f"Ano inválido detectado: {year_str}",
                "evidence": {
                    "text_snippet": text[max(0, match.start()-50):match.end()+50],
                    "url": url,
                    "anchor": anchor,
                }
            })
        
        # WARN: 2023 aparece mas 2024 não (se base_year=2024)
        if self.base_year == 2024:
            if "2023" in text and "2024" not in text:
                results.append({
                    "rule": "R1_missing_base_year",
                    "severity": "WARN",
                    "message": f"Encontrado {self.base_year-1} mas falta {self.base_year}",
                    "evidence": {
                        "text_snippet": text[:200],
                        "url": url,
                        "anchor": anchor,
                    }
                })
        
        # FAIL: série truncada (ex: "2020 a 2023" quando base_year=2024)
        pattern_series = r"(\d{4})\s+a\s+(\d{4})"
        for match in re.finditer(pattern_series, text):
            start_year = int(match.group(1))
            end_year = int(match.group(2))
            if end_year == self.base_year - 1 and self.base_year not in text:
                results.append({
                    "rule": "R1_truncated_series",
                    "severity": "FAIL",
                    "message": f"Série deve ir até {self.base_year}, encontrado até {end_year}. Sugerir: {start_year} a {self.base_year}",
                    "evidence": {
                        "text_snippet": text[max(0, match.start()-50):match.end()+50],
                        "url": url,
                        "anchor": anchor,
                    }
                })
        
        return results
    
    # ===== R2: Decimal Separator =====
    def r2_decimal_separator(self, text: str, url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """
        R2: Separador decimal
        WARN se detectar decimal com ponto (15.84 vs 15,84).
        Não confundir com milhares 1.769.277.
        """
        results = []
        
        # Procura padrão: número com ponto que não é milhares
        # Heurística: X.YY onde YY tem 1-2 dígitos (decimal, não milhares)
        pattern = r"\b(\d+)\.(\d{1,2})\b"
        matches = list(re.finditer(pattern, text))
        
        if matches:
            # Se encontrou alguns matches, avisar
            sample_matches = matches[:3]
            snippets = []
            for match in sample_matches:
                snippet = text[max(0, match.start()-30):match.end()+30]
                snippets.append(f"'{match.group(0)}'")
            
            results.append({
                "rule": "R2_decimal_separator",
                "severity": "WARN",
                "message": f"Decimal com ponto detectado. Verificar se é decimal (15.84) ou milhares. Exemplos: {', '.join(snippets)}",
                "evidence": {
                    "text_snippet": text[matches[0].start()-50:matches[0].end()+50],
                    "count_matches": len(matches),
                    "url": url,
                    "anchor": anchor,
                }
            })
        
        return results
    
    # ===== R3: Table Source Required =====
    def r3_table_source_required(self, table_data: Dict[str, Any], url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """R3: Tabela deve ter "Fonte:" em caption ou notes."""
        results = []
        
        caption = table_data.get("caption", "")
        notes = table_data.get("notes_text", "")
        
        if "Fonte:" not in caption and "Fonte:" not in notes:
            results.append({
                "rule": "R3_table_source_required",
                "severity": "FAIL",
                "message": f"Tabela sem 'Fonte:' detectável. Caption: '{caption[:100]}'",
                "evidence": {
                    "caption": caption,
                    "notes_preview": notes[:200],
                    "url": url,
                    "anchor": anchor,
                }
            })
        
        return results
    
    # ===== R4: Table Totals =====
    def r4_table_totals(self, table_data: Dict[str, Any], url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """R4: Se houver linha/coluna Total, recalcular e comparar."""
        results = []
        
        df = table_data.get("dataframe")
        if df is None or df.empty:
            return results
        
        # Procura linha "Total"
        total_row_idx = None
        for idx, row in df.iterrows():
            first_cell = str(row.iloc[0]).lower().strip()
            if "total" in first_cell:
                total_row_idx = idx
                break
        
        if total_row_idx is not None:
            # Recalcular totais para colunas numéricas
            try:
                numeric_cols = df.select_dtypes(include=["number"]).columns
                for col in numeric_cols:
                    try:
                        # Somar todas as linhas exceto a última (Total)
                        computed_sum = df.loc[~df.index.isin([total_row_idx]), col].sum()
                        reported_value = df.loc[total_row_idx, col]
                        
                        # Permitir pequena margem de erro (arredondamento)
                        if abs(float(reported_value) - float(computed_sum)) > 1:
                            results.append({
                                "rule": "R4_table_totals_mismatch",
                                "severity": "FAIL",
                                "message": f"Coluna '{col}' total mismatch: informado {reported_value}, calculado {computed_sum}",
                                "evidence": {
                                    "column": str(col),
                                    "reported": float(reported_value),
                                    "calculated": float(computed_sum),
                                    "url": url,
                                    "anchor": anchor,
                                }
                            })
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                logger.warning(f"Erro ao validar totais: {e}")
        
        return results
    
    # ===== R5: Table Completeness =====
    def r5_table_completeness(self, table_data: Dict[str, Any], url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """
        R5: Verificar integridade da tabela.
        - WARN para células vazias
        - FAIL para "ND" sem nota explicativa
        """
        results = []
        
        df = table_data.get("dataframe")
        notes = table_data.get("notes_text", "")
        
        if df is None:
            results.append({
                "rule": "R5_table_unreadable",
                "severity": "FAIL",
                "message": "Tabela não pode ser lida/parseada",
                "evidence": {
                    "caption": table_data.get("caption", ""),
                    "url": url,
                    "anchor": anchor,
                }
            })
            return results
        
        # Procura "ND" (Dado Não Disponível)
        nd_count = 0
        for col in df.columns:
            for val in df[col]:
                if str(val).strip().upper() == "ND":
                    nd_count += 1
        
        if nd_count > 0:
            if "ND:" not in notes and "não disponível" not in notes.lower():
                results.append({
                    "rule": "R5_nd_without_explanation",
                    "severity": "FAIL",
                    "message": f"Encontrado {nd_count} células com 'ND' mas sem explicação nas notas",
                    "evidence": {
                        "nd_count": nd_count,
                        "notes_preview": notes[:200],
                        "url": url,
                        "anchor": anchor,
                    }
                })
        
        # Procura células vazias
        empty_count = df.isna().sum().sum()
        if empty_count > 0:
            results.append({
                "rule": "R5_empty_cells",
                "severity": "WARN",
                "message": f"Tabela com {empty_count} células vazias",
                "evidence": {
                    "empty_count": int(empty_count),
                    "shape": df.shape,
                    "url": url,
                    "anchor": anchor,
                }
            })
        
        return results
    
    # ===== R6: Total Row Style =====
    def r6_total_row_style(self, table_data: Dict[str, Any], url: str, anchor: str = "") -> List[Dict[str, Any]]:
        """R6: Se houver linha Total, verificar se tem destaque (class/style ou <strong>)."""
        results = []
        
        table_html = table_data.get("table_html", "")
        
        # Procura <tr> com "Total" e verifica se tem destaque
        pattern_tr = r'<tr[^>]*>.*?<t[dh][^>]*>\s*Total\s*</t[dh]>.*?</tr>'
        
        for match in re.finditer(pattern_tr, table_html, re.IGNORECASE | re.DOTALL):
            tr_content = match.group(0)
            
            # Verificar se tem background, font-weight, <strong>, <b>
            has_style = any([
                "background" in tr_content.lower(),
                "font-weight" in tr_content.lower(),
                "<strong>" in tr_content.lower() or "<strong" in tr_content.lower(),
                "<b>" in tr_content.lower(),
            ])
            
            if not has_style:
                results.append({
                    "rule": "R6_total_row_no_highlight",
                    "severity": "WARN",
                    "message": "Linha Total sem destaque visual (background/font-weight/<strong>/<b>)",
                    "evidence": {
                        "html_snippet": tr_content[:200],
                        "url": url,
                        "anchor": anchor,
                    }
                })
        
        return results
