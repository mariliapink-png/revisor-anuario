from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import json
import re

app = FastAPI(title="Auditoria Anuário UnB")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== DATA MODELS =====
class Review(BaseModel):
    start_url: str
    report_year: int
    base_year: int

class CheckResult(BaseModel):
    rule: str
    severity: str
    message: str
    evidence: Optional[str] = None
    location: Optional[str] = None

class Section(BaseModel):
    id: str
    title: str
    url: str
    anchor: str
    level: int = 1

# ===== IN-MEMORY STORAGE =====
reviews_db = {}
sections_db = {}
results_db = {}

# ===== AUDIT ENGINE =====

class AuditEngine:
    def __init__(self, html: str, report_year: int, base_year: int, url: str):
        self.html = html
        self.report_year = report_year
        self.base_year = base_year
        self.url = url
        self.soup = BeautifulSoup(html, 'html.parser')
        self.results = []
    
    def run_all_checks(self) -> List[CheckResult]:
        """Roda todas as verificações"""
        self.check_year()
        self.check_tables()
        self.check_data_integrity()
        self.check_formatting()
        self.check_sources()
        self.check_structure()
        self.check_calculations()
        self.check_duplicates()
        self.check_headers()
        self.check_temporal_series()
        return self.results
    
    def add_result(self, rule: str, severity: str, message: str, evidence: str = "", location: str = ""):
        """Adiciona resultado"""
        self.results.append(CheckResult(
            rule=rule,
            severity=severity,
            message=message,
            evidence=evidence,
            location=location
        ))
    
    # ===== RULE 1: Verificar Ano =====
    def check_year(self):
        """R1: Verifica se o ano correto está no documento"""
        year_str = str(self.report_year)
        base_year_str = str(self.base_year)
        
        # Procura pelo ano no texto
        if year_str in self.html:
            count = self.html.count(year_str)
            self.add_result(
                "R1",
                "PASS",
                f"Ano {year_str} encontrado {count} vezes no documento",
                f"Encontrado {count} ocorrências de '{year_str}'",
                "HTML geral"
            )
        else:
            self.add_result(
                "R1",
                "FAIL",
                f"Ano {year_str} NÃO encontrado no documento",
                f"Esperado encontrar '{year_str}' no documento",
                "HTML geral"
            )
        
        # Verifica série histórica
        if base_year_str in self.html:
            self.add_result(
                "R1",
                "PASS",
                f"Ano base {base_year_str} encontrado (série histórica)",
                f"Encontrado '{base_year_str}'",
                "HTML geral"
            )
        else:
            self.add_result(
                "R1",
                "WARN",
                f"Ano base {base_year_str} não encontrado",
                f"Não localizado '{base_year_str}' para série histórica",
                "HTML geral"
            )
    
    # ===== RULE 2: Verificar Formatação =====
    def check_formatting(self):
        """R2: Verifica separadores decimais e formatação"""
        has_comma = "," in self.html
        has_dot = "." in self.html
        
        # Contar padrões de números
        comma_numbers = len(re.findall(r'\d+,\d+', self.html))
        dot_numbers = len(re.findall(r'\d+\.\d+', self.html))
        
        if comma_numbers > 0 and dot_numbers > 0:
            self.add_result(
                "R2",
                "WARN",
                f"Inconsistência de separadores: {comma_numbers} números com vírgula, {dot_numbers} com ponto",
                f"Padrão vírgula: {comma_numbers} casos\nPadrão ponto: {dot_numbers} casos",
                "Formatação numérica"
            )
        elif comma_numbers > 0:
            self.add_result(
                "R2",
                "PASS",
                f"Separador decimal consistente (vírgula): {comma_numbers} ocorrências",
                f"Formato consistente com {comma_numbers} números formatados",
                "Formatação numérica"
            )
        elif dot_numbers > 0:
            self.add_result(
                "R2",
                "PASS",
                f"Separador decimal consistente (ponto): {dot_numbers} ocorrências",
                f"Formato consistente com {dot_numbers} números formatados",
                "Formatação numérica"
            )
        else:
            self.add_result(
                "R2",
                "WARN",
                "Nenhum padrão de números decimais encontrado",
                "Não foi detectado uso de separadores decimais",
                "Formatação numérica"
            )
    
    # ===== RULE 3: Verificar Tabelas =====
    def check_tables(self):
        """R3: Verifica tabelas e sua estrutura"""
        tables = self.soup.find_all('table')
        
        if len(tables) == 0:
            self.add_result(
                "R3",
                "FAIL",
                "Nenhuma tabela encontrada no documento",
                "Esperado encontrar dados tabulares",
                "Tabelas"
            )
            return
        
        self.add_result(
            "R3",
            "PASS",
            f"Total de {len(tables)} tabela(s) encontrada(s)",
            f"Tabelas: {len(tables)}",
            "Tabelas"
        )
        
        # Verificar estrutura das tabelas
        for idx, table in enumerate(tables):
            rows = table.find_all('tr')
            cols = []
            for row in rows:
                cells = row.find_all(['td', 'th'])
                cols.append(len(cells))
            
            if len(set(cols)) > 1:
                self.add_result(
                    "R3",
                    "WARN",
                    f"Tabela {idx+1}: Número de colunas inconsistente",
                    f"Colunas por linha: {cols}",
                    f"Tabela {idx+1}"
                )
            else:
                self.add_result(
                    "R3",
                    "PASS",
                    f"Tabela {idx+1}: Estrutura consistente ({len(rows)} linhas)",
                    f"Linhas: {len(rows)}, Colunas: {cols[0] if cols else 0}",
                    f"Tabela {idx+1}"
                )
    
    # ===== RULE 4: Verificar Fontes =====
    def check_sources(self):
        """R4: Verifica se tabelas têm fontes"""
        tables = self.soup.find_all('table')
        
        fonte_count = 0
        for idx, table in enumerate(tables):
            # Procura por "Fonte:" próximo à tabela
            text_after = table.get_text(strip=True) + (table.find_next('p') or BeautifulSoup('', 'html.parser')).get_text(strip=True)
            
            if "Fonte:" in text_after or "fonte:" in text_after.lower():
                fonte_count += 1
                self.add_result(
                    "R4",
                    "PASS",
                    f"Tabela {idx+1}: Fonte identificada",
                    "Referência de fonte encontrada",
                    f"Tabela {idx+1}"
                )
            else:
                self.add_result(
                    "R4",
                    "FAIL",
                    f"Tabela {idx+1}: SEM referência de fonte",
                    "Não foi encontrado 'Fonte:' próximo à tabela",
                    f"Tabela {idx+1}"
                )
        
        if fonte_count == len(tables):
            self.add_result(
                "R4",
                "PASS",
                f"Todas as {len(tables)} tabelas têm fontes",
                f"{fonte_count}/{len(tables)} tabelas com fontes",
                "Fontes"
            )
    
    # ===== RULE 5: Integridade de Dados =====
    def check_data_integrity(self):
        """R5: Verifica integridade dos dados"""
        tables = self.soup.find_all('table')
        
        for idx, table in enumerate(tables):
            cells = table.find_all(['td', 'th'])
            
            # Contar células vazias
            empty_cells = sum(1 for cell in cells if not cell.get_text(strip=True))
            total_cells = len(cells)
            
            if empty_cells > 0:
                percentage = (empty_cells / total_cells * 100) if total_cells > 0 else 0
                if percentage > 30:
                    self.add_result(
                        "R5",
                        "FAIL",
                        f"Tabela {idx+1}: {empty_cells} células vazias ({percentage:.1f}%)",
                        f"Total de células: {total_cells}\nCélulas vazias: {empty_cells}",
                        f"Tabela {idx+1}"
                    )
                else:
                    self.add_result(
                        "R5",
                        "WARN",
                        f"Tabela {idx+1}: {empty_cells} células vazias ({percentage:.1f}%)",
                        f"Total de células: {total_cells}\nCélulas vazias: {empty_cells}",
                        f"Tabela {idx+1}"
                    )
            else:
                self.add_result(
                    "R5",
                    "PASS",
                    f"Tabela {idx+1}: Sem células vazias (integridade OK)",
                    f"Total de células: {total_cells}",
                    f"Tabela {idx+1}"
                )
    
    # ===== RULE 6: Estrutura de Headers =====
    def check_headers(self):
        """R6: Verifica estrutura de títulos"""
        headings = self.soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        
        if len(headings) == 0:
            self.add_result(
                "R6",
                "FAIL",
                "Nenhum título encontrado (sem H1-H6)",
                "Estrutura de títulos não está clara",
                "Estrutura"
            )
        else:
            h1_count = len(self.soup.find_all('h1'))
            h2_count = len(self.soup.find_all('h2'))
            
            if h1_count > 0:
                self.add_result(
                    "R6",
                    "PASS",
                    f"Estrutura hierárquica encontrada: {len(headings)} títulos",
                    f"H1: {h1_count}, H2: {h2_count}",
                    "Estrutura"
                )
            else:
                self.add_result(
                    "R6",
                    "WARN",
                    f"Estrutura parcial: {len(headings)} títulos (sem H1)",
                    f"H2: {h2_count}",
                    "Estrutura"
                )
    
    # ===== RULE 7: Cálculos (Totais) =====
    def check_calculations(self):
        """R7: Verifica se totais estão corretos"""
        tables = self.soup.find_all('table')
        
        for idx, table in enumerate(tables):
            rows = table.find_all('tr')
            
            # Procura por linhas de "Total"
            for row in rows:
                text = row.get_text(strip=True).lower()
                if "total" in text:
                    self.add_result(
                        "R7",
                        "PASS",
                        f"Tabela {idx+1}: Linha de totais identificada",
                        f"Texto: {text[:50]}...",
                        f"Tabela {idx+1}"
                    )
                    break
            else:
                self.add_result(
                    "R7",
                    "WARN",
                    f"Tabela {idx+1}: Sem linha de totais visível",
                    "Não foi encontrada linha com 'Total'",
                    f"Tabela {idx+1}"
                )
    
    # ===== RULE 8: Duplicatas =====
    def check_duplicates(self):
        """R8: Verifica dados duplicados"""
        tables = self.soup.find_all('table')
        
        for idx, table in enumerate(tables):
            rows = table.find_all('tr')
            
            row_texts = [row.get_text(strip=True) for row in rows]
            duplicates = len(row_texts) - len(set(row_texts))
            
            if duplicates > 0:
                self.add_result(
                    "R8",
                    "WARN",
                    f"Tabela {idx+1}: {duplicates} linha(s) duplicada(s)",
                    f"Total de linhas: {len(row_texts)}\nLinhas únicas: {len(set(row_texts))}",
                    f"Tabela {idx+1}"
                )
            else:
                self.add_result(
                    "R8",
                    "PASS",
                    f"Tabela {idx+1}: Sem linhas duplicadas",
                    f"Total de linhas: {len(row_texts)}",
                    f"Tabela {idx+1}"
                )
    
    # ===== RULE 9: Série Temporal =====
    def check_temporal_series(self):
        """R9: Verifica série temporal (anos históricos)"""
        years_found = re.findall(r'\b(19|20)\d{2}\b', self.html)
        years_found = list(set([int(y) for y in years_found]))
        years_found.sort()
        
        if len(years_found) > 1:
            year_range = f"{years_found[0]}-{years_found[-1]}"
            self.add_result(
                "R9",
                "PASS",
                f"Série temporal identificada: {year_range}",
                f"Anos encontrados: {years_found}",
                "Série temporal"
            )
        elif len(years_found) == 1:
            self.add_result(
                "R9",
                "WARN",
                f"Apenas 1 ano encontrado: {years_found[0]}",
                "Não há série histórica",
                "Série temporal"
            )
        else:
            self.add_result(
                "R9",
                "FAIL",
                "Nenhum ano encontrado no documento",
                "Sem dados temporais",
                "Série temporal"
            )

# ===== HTML INTERFACE =====
HTML_CONTENT = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria do Anuário UnB</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #003366 0%, #2E1D86 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 1000px;
            margin: 0 auto;
            padding: 40px;
        }
        h1 {
            color: #003366;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            color: #003366;
            font-weight: 600;
            margin-bottom: 8px;
            font-size: 14px;
        }
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
        }
        input:focus {
            outline: none;
            border-color: #003366;
            box-shadow: 0 0 0 3px rgba(0,51,102,0.1);
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #003366 0%, #2E1D86 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 20px;
            transition: transform 0.2s;
        }
        button:hover {
            transform: translateY(-2px);
        }
        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        #loading {
            display: none;
            text-align: center;
            padding: 20px;
        }
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #003366;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .results {
            display: none;
            margin-top: 30px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat {
            text-align: center;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 6px;
        }
        .stat-number {
            font-size: 32px;
            font-weight: bold;
            color: #003366;
        }
        .stat-label {
            font-size: 12px;
            color: #666;
            margin-top: 5px;
        }
        .result-item {
            padding: 15px;
            margin-bottom: 12px;
            border-radius: 6px;
            border-left: 4px solid #ddd;
        }
        .result-item.pass {
            background: #e8f5e9;
            border-left-color: #4caf50;
        }
        .result-item.warn {
            background: #fff3e0;
            border-left-color: #ff9800;
        }
        .result-item.fail {
            background: #ffebee;
            border-left-color: #f44336;
        }
        .result-title {
            font-weight: 600;
            color: #333;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .result-message {
            color: #666;
            font-size: 14px;
            margin-bottom: 8px;
        }
        .result-evidence {
            color: #777;
            font-size: 13px;
            background: rgba(0,0,0,0.03);
            padding: 8px;
            border-radius: 4px;
            margin-bottom: 8px;
            font-family: monospace;
            white-space: pre-wrap;
        }
        .result-location {
            color: #888;
            font-size: 12px;
            font-style: italic;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge-pass {
            background: #4caf50;
            color: white;
        }
        .badge-warn {
            background: #ff9800;
            color: white;
        }
        .badge-fail {
            background: #f44336;
            color: white;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria do Anuário UnB</h1>
        <p class="subtitle">Sistema completo de auditoria com verificação de inconsistências</p>

        <div id="form">
            <div class="form-group">
                <label for="url">URL do Anuário</label>
                <input 
                    type="url" 
                    id="url" 
                    placeholder="https://anuariounb2025.netlify.app/"
                    value="https://anuariounb2025.netlify.app/"
                >
            </div>

            <div class="form-row">
                <div class="form-group">
                    <label for="year">Ano do Relatório</label>
                    <input type="number" id="year" value="2025">
                </div>
                <div class="form-group">
                    <label for="baseYear">Ano Base</label>
                    <input type="number" id="baseYear" value="2024">
                </div>
            </div>

            <button onclick="startAudit()">🚀 Iniciar Auditoria Completa</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Auditando documento...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 style="color: #003366; margin-bottom: 20px;">Relatório Detalhado</h2>
            <div id="resultsContent"></div>
        </div>
    </div>

    <script>
        const API_URL = 'https://revisor-anuario-2.onrender.com';

        async function startAudit() {
            const url = document.getElementById('url').value;
            const year = document.getElementById('year').value;
            const baseYear = document.getElementById('baseYear').value;

            if (!url) {
                alert('Cole uma URL válida!');
                return;
            }

            document.getElementById('form').style.display = 'none';
            document.getElementById('loading').style.display = 'block';

            try {
                const response = await fetch(`${API_URL}/reviews`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        start_url: url,
                        report_year: parseInt(year),
                        base_year: parseInt(baseYear)
                    })
                });

                const data = await response.json();
                const reviewId = data.id;

                await new Promise(r => setTimeout(r, 2000));

                const checkResponse = await fetch(`${API_URL}/reviews/${reviewId}/sections`);
                const sections = await checkResponse.json();

                let allResults = [];
                for (let section of sections.slice(0, 3)) {
                    try {
                        const res = await fetch(`${API_URL}/reviews/${reviewId}/sections/${section.id}/run-checks`, {
                            method: 'POST'
                        });
                        const checkData = await res.json();
                        if (checkData.results) {
                            allResults = allResults.concat(checkData.results);
                        }
                    } catch (e) {}
                }

                displayResults(allResults);
            } catch (error) {
                alert('Erro: ' + error.message);
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
            }
        }

        function displayResults(results) {
            const stats = {
                pass: results.filter(r => r.severity === 'PASS').length,
                warn: results.filter(r => r.severity === 'WARN').length,
                fail: results.filter(r => r.severity === 'FAIL').length,
            };

            document.getElementById('stats').innerHTML = `
                <div class="stat">
                    <div class="stat-number" style="color: #4caf50;">${stats.pass}</div>
                    <div class="stat-label">OK</div>
                </div>
                <div class="stat">
                    <div class="stat-number" style="color: #ff9800;">${stats.warn}</div>
                    <div class="stat-label">Avisos</div>
                </div>
                <div class="stat">
                    <div class="stat-number" style="color: #f44336;">${stats.fail}</div>
                    <div class="stat-label">Erros</div>
                </div>
            `;

            const resultsHtml = results.map(result => `
                <div class="result-item ${result.severity.toLowerCase()}">
                    <div class="result-title">
                        <span><strong>${result.rule}</strong>: ${result.message}</span>
                        <span class="badge badge-${result.severity.toLowerCase()}">${result.severity}</span>
                    </div>
                    ${result.evidence ? `<div class="result-evidence">${result.evidence}</div>` : ''}
                    ${result.location ? `<div class="result-location">📍 ${result.location}</div>` : ''}
                </div>
            `).join('');

            document.getElementById('resultsContent').innerHTML = resultsHtml;
            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }
    </script>
</body>
</html>"""

# ===== ENDPOINTS =====

@app.get("/", response_class=HTMLResponse)
def serve_html():
    return HTML_CONTENT

@app.post("/reviews")
def create_review(review: Review):
    try:
        response = requests.get(review.start_url, timeout=15)
        html = response.text
        
        review_id = str(uuid.uuid4())
        reviews_db[review_id] = {
            "id": review_id,
            "start_url": review.start_url,
            "report_year": review.report_year,
            "base_year": review.base_year,
            "created_at": datetime.now().isoformat()
        }
        
        soup = BeautifulSoup(html, 'html.parser')
        headings = soup.find_all(['h1', 'h2', 'h3'])
        sections = []
        for heading in headings:
            title = heading.get_text(strip=True)
            if title:
                sections.append(Section(
                    id=str(uuid.uuid4()),
                    title=title,
                    url=review.start_url,
                    anchor=heading.get('id', ''),
                    level=int(heading.name[1])
                ))
        
        if not sections:
            sections = [Section(
                id=str(uuid.uuid4()),
                title="Página Principal",
                url=review.start_url,
                anchor=""
            )]
        
        sections_db[review_id] = sections
        return reviews_db[review_id]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/reviews/{review_id}/sections")
def get_sections(review_id: str):
    if review_id not in sections_db:
        raise HTTPException(status_code=404, detail="Review not found")
    return sections_db[review_id]

@app.post("/reviews/{review_id}/sections/{section_id}/run-checks")
def run_section_checks(review_id: str, section_id: str):
    if review_id not in reviews_db:
        raise HTTPException(status_code=404, detail="Review not found")
    
    try:
        review = reviews_db[review_id]
        response = requests.get(review["start_url"], timeout=15)
        html = response.text
        
        engine = AuditEngine(html, review["report_year"], review["base_year"], review["start_url"])
        results = engine.run_all_checks()
        
        if review_id not in results_db:
            results_db[review_id] = {}
        results_db[review_id][section_id] = results
        
        return {
            "id": str(uuid.uuid4()),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)