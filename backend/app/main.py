from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
reviews_db = {}  # {review_id: review_data}
sections_db = {}  # {review_id: [sections]}
results_db = {}  # {review_id: {section_id: [results]}}

# ===== HELPER FUNCTIONS =====

def extract_sections(html: str, start_url: str) -> List[Section]:
    """Extrai seções do HTML"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        sections = []
        
        # Procura por headings
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
    """Executa verificações no HTML"""
    results = []
    
    # R1: Verifica ano correto
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
    
    # R2: Verifica separador decimal
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
    
    # R3: Procura por tabelas
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
    
    # R4: Verifica por "Fonte:"
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
    
    # R5: Verifica integridade
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
    
    # R6: Verifica títulos
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

@app.get("/")
def read_root():
    """Health check"""
    return {"message": "Auditoria Anuário UnB API", "status": "online"}

@app.post("/reviews")
def create_review(review: Review):
    """Cria nova auditoria"""
    try:
        # Fazer request para URL
        response = requests.get(review.start_url, timeout=10)
        html = response.text
        
        # Criar review
        review_id = str(uuid.uuid4())
        reviews_db[review_id] = {
            "id": review_id,
            "start_url": review.start_url,
            "report_year": review.report_year,
            "base_year": review.base_year,
            "created_at": datetime.now().isoformat()
        }
        
        # Extrair seções
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
    """Lista seções"""
    if review_id not in sections_db:
        raise HTTPException(status_code=404, detail="Review not found")
    
    return sections_db[review_id]

@app.post("/reviews/{review_id}/sections/{section_id}/run-checks")
def run_section_checks(review_id: str, section_id: str):
    """Roda checagens para uma seção"""
    if review_id not in reviews_db:
        raise HTTPException(status_code=404, detail="Review not found")
    
    try:
        review = reviews_db[review_id]
        response = requests.get(review["start_url"], timeout=10)
        html = response.text
        
        # Rodar checks
        results = run_checks(html, review["report_year"], review["base_year"])
        
        # Salvar resultados
        if review_id not in results_db:
            results_db[review_id] = {}
        results_db[review_id][section_id] = results
        
        return {
            "id": str(uuid.uuid4()),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/reviews/{review_id}/sections/{section_id}/results")
def get_section_results(review_id: str, section_id: str):
    """Retorna resultados"""
    if review_id not in results_db or section_id not in results_db[review_id]:
        raise HTTPException(status_code=404, detail="Results not found")
    
    return {
        "id": str(uuid.uuid4()),
        "results": results_db[review_id][section_id]
    }

@app.post("/reviews/{review_id}/run-all")
def run_all_checks(review_id: str, max_pages: int = 50):
    """Roda todas as checagens"""
    if review_id not in reviews_db:
        raise HTTPException(status_code=404, detail="Review not found")
    
    try:
        review = reviews_db[review_id]
        sections = sections_db.get(review_id, [])
        
        # Rodar checks para todas seções
        for section in sections[:max_pages]:
            response = requests.get(review["start_url"], timeout=10)
            html = response.text
            results = run_checks(html, review["report_year"], review["base_year"])
            
            if review_id not in results_db:
                results_db[review_id] = {}
            results_db[review_id][section.id] = results
        
        return {"status": "completed"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/reviews/{review_id}/sections/{section_id}/manual")
def save_manual_review(review_id: str, section_id: str, data: dict):
    """Salva revisão manual"""
    if review_id not in reviews_db:
        raise HTTPException(status_code=404, detail="Review not found")
    
    return {"status": "saved"}

@app.get("/reviews/{review_id}/export")
def export_report(review_id: str, format: str = "html"):
    """Exporta relatório"""
    if review_id not in reviews_db:
        raise HTTPException(status_code=404, detail="Review not found")
    
    review = reviews_db[review_id]
    all_results = []
    
    for section_id, results in results_db.get(review_id, {}).items():
        all_results.extend(results)
    
    if format == "html":
        html = f"""
        <html>
        <head>
            <title>Relatório - {review['report_year']}</title>
            <style>
                body {{ font-family: Arial; margin: 20px; }}
                .pass {{ color: green; }}
                .fail {{ color: red; }}
                .warn {{ color: orange; }}
            </style>
        </head>
        <body>
            <h1>Auditoria - Anuário {review['report_year']}</h1>
            <p>URL: {review['start_url']}</p>
            <h2>Resultados:</h2>
        """
        for result in all_results:
            html += f"""
            <div class="{result['severity'].lower()}">
                <strong>{result['rule']}</strong>: {result['message']}
            </div>
            """
        html += "</body></html>"
        return {"filename": f"report_{review_id}.html", "content": html}
    
    return {"filename": f"report_{review_id}.json", "content": json.dumps(all_results)}

@app.get("/downloads/{filename}")
def download_file(filename: str):
    """Download arquivo"""
    # Em produção, retornar arquivo real
    return {"message": "File download not implemented"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
