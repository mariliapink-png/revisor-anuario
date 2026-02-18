from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re

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

class CheckResult(BaseModel):
    rule: str
    severity: str
    message: str
    evidence: Optional[str] = None
    location: Optional[str] = None

def run_checks(html: str, report_year: int, base_year: int) -> List[CheckResult]:
    """Roda todas as checagens"""
    results = []
    
    year_str = str(report_year)
    base_year_str = str(base_year)
    
    # R1: Ano
    year_count = html.count(year_str)
    if year_count > 0:
        results.append(CheckResult(
            rule="R1",
            severity="PASS",
            message=f"Ano {year_str} encontrado {year_count} vezes",
            evidence=f"Ocorrências: {year_count}",
            location="Documento"
        ))
    else:
        results.append(CheckResult(
            rule="R1",
            severity="FAIL",
            message=f"Ano {year_str} não encontrado",
            evidence="Esperado localizar o ano",
            location="Documento"
        ))
    
    # R2: Formatação
    virgula = len(re.findall(r'\d{1,3},\d{2,}', html))
    ponto = len(re.findall(r'\d{1,3}\.\d{2,}', html))
    
    if virgula > 0 and ponto > 0:
        results.append(CheckResult(
            rule="R2",
            severity="WARN",
            message=f"Mistura de separadores: {virgula} com vírgula, {ponto} com ponto",
            evidence=f"Vírgula: {virgula}\nPonto: {ponto}",
            location="Formatação"
        ))
    elif virgula > 0:
        results.append(CheckResult(
            rule="R2",
            severity="PASS",
            message=f"Separador decimal consistente (vírgula): {virgula}",
            evidence=f"Encontrados: {virgula}",
            location="Formatação"
        ))
    elif ponto > 0:
        results.append(CheckResult(
            rule="R2",
            severity="PASS",
            message=f"Separador decimal consistente (ponto): {ponto}",
            evidence=f"Encontrados: {ponto}",
            location="Formatação"
        ))
    else:
        results.append(CheckResult(
            rule="R2",
            severity="WARN",
            message="Sem padrão de números decimais",
            evidence="Nenhum número com separador",
            location="Formatação"
        ))
    
    # R3: Tabelas
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    
    if len(tables) > 0:
        results.append(CheckResult(
            rule="R3",
            severity="PASS",
            message=f"{len(tables)} tabela(s) encontrada(s)",
            evidence=f"Total: {len(tables)}",
            location="Tabelas"
        ))
        
        for idx, table in enumerate(tables, 1):
            rows = table.find_all('tr')
            cols_per_row = [len(row.find_all(['td', 'th'])) for row in rows]
            
            if len(set(cols_per_row)) == 1:
                results.append(CheckResult(
                    rule="R3",
                    severity="PASS",
                    message=f"Tabela {idx}: Estrutura OK ({len(rows)} linhas)",
                    evidence=f"Linhas: {len(rows)}, Colunas: {cols_per_row[0] if cols_per_row else 0}",
                    location=f"Tabela {idx}"
                ))
            else:
                results.append(CheckResult(
                    rule="R3",
                    severity="WARN",
                    message=f"Tabela {idx}: Número de colunas inconsistente",
                    evidence=f"Por linha: {cols_per_row}",
                    location=f"Tabela {idx}"
                ))
    else:
        results.append(CheckResult(
            rule="R3",
            severity="FAIL",
            message="Nenhuma tabela encontrada",
            evidence="Documento sem dados tabulares",
            location="Tabelas"
        ))
    
    # R4: Fontes
    fonte_count = len(re.findall(r'[Ff]onte\s*:', html))
    
    if len(tables) > 0:
        if fonte_count >= len(tables):
            results.append(CheckResult(
                rule="R4",
                severity="PASS",
                message=f"Todas as tabelas têm fontes",
                evidence=f"Fontes: {fonte_count}",
                location="Fontes"
            ))
        elif fonte_count > 0:
            results.append(CheckResult(
                rule="R4",
                severity="WARN",
                message=f"Apenas {fonte_count} de {len(tables)} tabelas têm fontes",
                evidence=f"Proporção: {fonte_count}/{len(tables)}",
                location="Fontes"
            ))
        else:
            results.append(CheckResult(
                rule="R4",
                severity="FAIL",
                message="Nenhuma tabela tem fonte",
                evidence="Sem 'Fonte:' encontrado",
                location="Fontes"
            ))
    
    # R5: Integridade
    for idx, table in enumerate(tables, 1):
        cells = table.find_all(['td', 'th'])
        vazias = sum(1 for c in cells if not c.get_text(strip=True))
        total = len(cells)
        pct = (vazias / total * 100) if total > 0 else 0
        
        if pct == 0:
            results.append(CheckResult(
                rule="R5",
                severity="PASS",
                message=f"Tabela {idx}: Sem células vazias",
                evidence=f"Células: {total}",
                location=f"Tabela {idx}"
            ))
        else:
            results.append(CheckResult(
                rule="R5",
                severity="WARN" if pct < 20 else "FAIL",
                message=f"Tabela {idx}: {vazias} células vazias ({pct:.1f}%)",
                evidence=f"Vazias: {vazias}/{total}",
                location=f"Tabela {idx}"
            ))
    
    # R6: Headers
    headers = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    
    if len(headers) > 0:
        h1 = len(soup.find_all('h1'))
        results.append(CheckResult(
            rule="R6",
            severity="PASS" if h1 > 0 else "WARN",
            message=f"Estrutura: {len(headers)} títulos" + (" com H1" if h1 > 0 else " sem H1"),
            evidence=f"Total: {len(headers)}",
            location="Estrutura"
        ))
    else:
        results.append(CheckResult(
            rule="R6",
            severity="FAIL",
            message="Nenhum título encontrado",
            evidence="Sem H1-H6",
            location="Estrutura"
        ))
    
    # R7: Totais
    for idx, table in enumerate(tables, 1):
        rows = table.find_all('tr')
        tem_total = any('total' in row.get_text().lower() for row in rows)
        
        results.append(CheckResult(
            rule="R7",
            severity="PASS" if tem_total else "WARN",
            message=f"Tabela {idx}: {'Linha de totais encontrada' if tem_total else 'Sem linha de totais'}",
            evidence="'Total' " + ("encontrado" if tem_total else "não encontrado"),
            location=f"Tabela {idx}"
        ))
    
    # R8: Duplicatas
    for idx, table in enumerate(tables, 1):
        rows = table.find_all('tr')
        texts = [r.get_text(strip=True)[:30] for r in rows]
        dups = len(texts) - len(set(texts))
        
        results.append(CheckResult(
            rule="R8",
            severity="PASS" if dups == 0 else "WARN",
            message=f"Tabela {idx}: {dups} linha(s) duplicada(s)" if dups > 0 else f"Tabela {idx}: Sem duplicatas",
            evidence=f"Total: {len(texts)}, Únicas: {len(set(texts))}",
            location=f"Tabela {idx}"
        ))
    
    # R9: Anos
    anos = sorted(set(int(m) for m in re.findall(r'\b(19|20)\d{2}\b', html)))
    
    if len(anos) > 1:
        results.append(CheckResult(
            rule="R9",
            severity="PASS",
            message=f"Série temporal: {anos[0]}-{anos[-1]}",
            evidence=f"Anos: {anos}",
            location="Temporal"
        ))
    elif len(anos) == 1:
        results.append(CheckResult(
            rule="R9",
            severity="WARN",
            message=f"Apenas um ano: {anos[0]}",
            evidence="Sem série histórica",
            location="Temporal"
        ))
    else:
        results.append(CheckResult(
            rule="R9",
            severity="FAIL",
            message="Nenhum ano encontrado",
            evidence="Sem dados temporais",
            location="Temporal"
        ))
    
    return results

# ===== HTML =====
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria UnB</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
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
        h1 { color: #003366; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #003366; font-weight: 600; margin-bottom: 8px; }
        input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; }
        input:focus { outline: none; border-color: #003366; box-shadow: 0 0 0 3px rgba(0,51,102,0.1); }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
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
        }
        #loading { display: none; text-align: center; padding: 20px; }
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #003366;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .results { display: none; margin-top: 30px; }
        .stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat { text-align: center; padding: 20px; background: #f8f9fa; border-radius: 6px; }
        .stat-number { font-size: 32px; font-weight: bold; color: #003366; }
        .stat-label { font-size: 12px; color: #666; margin-top: 5px; }
        .result-item {
            padding: 15px;
            margin-bottom: 12px;
            border-radius: 6px;
            border-left: 4px solid #ddd;
        }
        .result-item.pass { background: #e8f5e9; border-left-color: #4caf50; }
        .result-item.warn { background: #fff3e0; border-left-color: #ff9800; }
        .result-item.fail { background: #ffebee; border-left-color: #f44336; }
        .result-title { font-weight: 600; color: #333; margin-bottom: 8px; display: flex; justify-content: space-between; }
        .result-message { color: #666; font-size: 14px; margin-bottom: 8px; }
        .result-evidence { color: #777; font-size: 13px; background: rgba(0,0,0,0.03); padding: 8px; border-radius: 4px; margin-bottom: 8px; font-family: monospace; white-space: pre-wrap; }
        .result-location { color: #888; font-size: 12px; font-style: italic; }
        .badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        .badge-pass { background: #4caf50; color: white; }
        .badge-warn { background: #ff9800; color: white; }
        .badge-fail { background: #f44336; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria do Anuário UnB</h1>
        <p class="subtitle">Verificação completa de inconsistências</p>

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
            <button onclick="audit()">🚀 Iniciar Auditoria</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Auditando...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 style="color: #003366; margin-bottom: 20px;">Relatório</h2>
            <div id="content"></div>
        </div>
    </div>

    <script>
        async function audit() {
            const url = document.getElementById('url').value;
            const year = parseInt(document.getElementById('year').value);
            const base = parseInt(document.getElementById('baseYear').value);

            document.getElementById('form').style.display = 'none';
            document.getElementById('loading').style.display = 'block';

            try {
                const res = await fetch('https://revisor-anuario-2.onrender.com/audit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, report_year: year, base_year: base })
                });

                const data = await res.json();
                showResults(data.results);
            } catch (e) {
                alert('Erro: ' + e.message);
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
            }
        }

        function showResults(results) {
            if (!results || results.length === 0) {
                alert('Sem resultados');
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
                return;
            }

            const pass = results.filter(r => r.severity === 'PASS').length;
            const warn = results.filter(r => r.severity === 'WARN').length;
            const fail = results.filter(r => r.severity === 'FAIL').length;

            document.getElementById('stats').innerHTML = `
                <div class="stat">
                    <div class="stat-number" style="color: #4caf50;">${pass}</div>
                    <div class="stat-label">OK</div>
                </div>
                <div class="stat">
                    <div class="stat-number" style="color: #ff9800;">${warn}</div>
                    <div class="stat-label">Avisos</div>
                </div>
                <div class="stat">
                    <div class="stat-number" style="color: #f44336;">${fail}</div>
                    <div class="stat-label">Erros</div>
                </div>
            `;

            document.getElementById('content').innerHTML = results.map(r => `
                <div class="result-item ${r.severity.toLowerCase()}">
                    <div class="result-title">
                        <strong>${r.rule}</strong>
                        <span class="badge badge-${r.severity.toLowerCase()}">${r.severity}</span>
                    </div>
                    <div class="result-message">${r.message}</div>
                    ${r.evidence ? `<div class="result-evidence">${r.evidence}</div>` : ''}
                    ${r.location ? `<div class="result-location">📍 ${r.location}</div>` : ''}
                </div>
            `).join('');

            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }
    </script>
</body>
</html>"""

# ===== ENDPOINTS =====

@app.get("/", response_class=HTMLResponse)
def serve():
    return HTML

@app.post("/audit")
def audit(req: AuditRequest):
    try:
        resp = requests.get(req.url, timeout=15)
        html = resp.text
        
        results = run_checks(html, req.report_year, req.base_year)
        
        return {
            "status": "ok",
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)