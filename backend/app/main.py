from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import json

app = FastAPI(title="Auditoria Anuário UnB")

# CORS
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
    evidence_json: Optional[dict] = None

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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #003366 0%, #2E1D86 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 800px;
            width: 100%;
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
            font-family: inherit;
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
            font-size: 14px;
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
            margin-bottom: 20px;
        }
        .stat {
            text-align: center;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 6px;
        }
        .stat-number {
            font-size: 28px;
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
            margin-bottom: 10px;
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
            margin-bottom: 5px;
        }
        .result-message {
            color: #666;
            font-size: 14px;
        }
        .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            margin-left: 10px;
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
        <p class="subtitle">Cole a URL do anuário e deixe o sistema fazer a auditoria automática</p>

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

            <button onclick="startAudit()">🚀 Iniciar Auditoria</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Processando auditoria...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
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
                for (let section of sections.slice(0, 5)) {
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
                        ${result.rule}
                        <span class="badge badge-${result.severity.toLowerCase()}">${result.severity}</span>
                    </div>
                    <div class="result-message">${result.message}</div>
                </div>
            `).join('');

            document.getElementById('resultsContent').innerHTML = resultsHtml;
            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }
    </script>
</body>
</html>"""

# ===== HELPER FUNCTIONS =====

def extract_sections(html: str, start_url: str) -> List[Section]:
    try:
        soup = BeautifulSoup(html, 'html.parser')
        sections = []
        
        for heading in soup.find_all(['h1', 'h2', 'h3']):
            title = heading.get_text(strip=True)
            if title:
                section_id = str(uuid.uuid4())
                sections.append(Section(
                    id=section_id,
                    title=title,
                    url=start_url,
                    anchor=heading.get('id', ''),
                    level=int(heading.name[1])
                ))
        
        return sections if sections else [Section(
            id=str(uuid.uuid4()),
            title="Página Principal",
            url=start_url,
            anchor=""
        )]
    except:
        return [Section(
            id=str(uuid.uuid4()),
            title="Página Principal",
            url=start_url,
            anchor=""
        )]

def run_checks(html: str, report_year: int, base_year: int) -> List[CheckResult]:
    results = []
    
    year_str = str(report_year)
    if year_str in html:
        results.append(CheckResult(
            rule="R1",
            severity="PASS",
            message=f"Ano {year_str} encontrado no documento"
        ))
    else:
        results.append(CheckResult(
            rule="R1",
            severity="FAIL",
            message=f"Ano {year_str} NÃO encontrado no documento"
        ))
    
    if "," in html and "." in html:
        results.append(CheckResult(
            rule="R2",
            severity="PASS",
            message="Separadores decimais encontrados (vírgula e ponto)"
        ))
    else:
        results.append(CheckResult(
            rule="R2",
            severity="WARN",
            message="Verificar consistência de separadores decimais"
        ))
    
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    if tables:
        results.append(CheckResult(
            rule="R3",
            severity="PASS",
            message=f"{len(tables)} tabela(s) encontrada(s)"
        ))
    else:
        results.append(CheckResult(
            rule="R3",
            severity="FAIL",
            message="Nenhuma tabela encontrada"
        ))
    
    if "Fonte:" in html or "fonte:" in html.lower():
        results.append(CheckResult(
            rule="R4",
            severity="PASS",
            message="Referências de fontes encontradas"
        ))
    else:
        results.append(CheckResult(
            rule="R4",
            severity="WARN",
            message="Verificar se todas as tabelas têm 'Fonte:'"
        ))
    
    if len(html) > 1000:
        results.append(CheckResult(
            rule="R5",
            severity="PASS",
            message="Documento tem conteúdo adequado"
        ))
    else:
        results.append(CheckResult(
            rule="R5",
            severity="FAIL",
            message="Documento pode estar incompleto"
        ))
    
    if "<h1" in html.lower() or "<h2" in html.lower():
        results.append(CheckResult(
            rule="R6",
            severity="PASS",
            message="Estrutura de títulos encontrada"
        ))
    else:
        results.append(CheckResult(
            rule="R6",
            severity="WARN",
            message="Estrutura de títulos não está clara"
        ))
    
    return results

# ===== ENDPOINTS =====

@app.get("/", response_class=HTMLResponse)
def serve_html():
    """Serve HTML interface at root"""
    return HTML_CONTENT

@app.post("/reviews")
def create_review(review: Review):
    try:
        response = requests.get(review.start_url, timeout=10)
        html = response.text
        
        review_id = str(uuid.uuid4())
        reviews_db[review_id] = {
            "id": review_id,
            "start_url": review.start_url,
            "report_year": review.report_year,
            "base_year": review.base_year,
            "created_at": datetime.now().isoformat()
        }
        
        sections = extract_sections(html, review.start_url)
        sections_db[review_id] = sections
        
        return {
            "id": review_id,
            "start_url": review.start_url,
            "report_year": review.report_year,
            "base_year": review.base_year,
            "created_at": datetime.now().isoformat()
        }
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
        response = requests.get(review["start_url"], timeout=10)
        html = response.text
        
        results = run_checks(html, review["report_year"], review["base_year"])
        
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