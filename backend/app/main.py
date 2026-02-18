from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from datetime import datetime
from pathlib import Path
import logging
from typing import List

from app.database import get_db, init_db
from app.schemas import (
    ReviewCreate, ReviewResponse, SectionResponse,
    CheckRunResponse, ManualReviewCreate, ManualReviewResponse
)
from app.models import Review, Section, CheckRun, CheckResult, ManualReview
from app.toc_extractor import TOCExtractor
from app.section_extractor import SectionExtractor
from app.check_engine import CheckEngine
from app.report_generator import ReportGenerator
from app.config import EXPORTS_DIR

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Auditoria Anuário UnB", version="0.1.0")


# ===== INICIALIZAÇÃO =====
@app.on_event("startup")
async def startup():
    """Inicializa banco de dados."""
    init_db()
    logger.info("Banco de dados inicializado")


# ===== ENDPOINTS =====

@app.post("/reviews", response_model=ReviewResponse)
async def create_review(
    review_data: ReviewCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Cria nova review e extrai TOC automaticamente.
    POST /reviews
    {
        "start_url": "https://anuariounb2025.netlify.app/",
        "report_year": 2025,
        "base_year": 2024
    }
    """
    # Verificar se já existe review para esta URL
    existing = db.query(Review).filter(Review.start_url == review_data.start_url).first()
    if existing:
        raise HTTPException(status_code=400, detail="Review para esta URL já existe")
    
    # Criar review
    review = Review(
        start_url=review_data.start_url,
        report_year=review_data.report_year,
        base_year=review_data.base_year,
        created_at=datetime.utcnow()
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    
    # Extrair TOC em background
    background_tasks.add_task(
        extract_toc_task,
        review_id=review.id,
        start_url=review_data.start_url
    )
    
    logger.info(f"Review criada: {review.id} para {review_data.start_url}")
    return review


@app.get("/reviews/{review_id}", response_model=ReviewResponse)
async def get_review(review_id: int, db: Session = Depends(get_db)):
    """GET /reviews/{id}"""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review não encontrada")
    return review


@app.get("/reviews/{review_id}/sections", response_model=List[SectionResponse])
async def list_sections(review_id: int, db: Session = Depends(get_db)):
    """GET /reviews/{id}/sections"""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review não encontrada")
    
    sections = db.query(Section).filter(Section.review_id == review_id).all()
    return sections


@app.post("/reviews/{review_id}/sections/{section_id}/run-checks", response_model=CheckRunResponse)
async def run_section_checks(
    review_id: int,
    section_id: int,
    db: Session = Depends(get_db)
):
    """
    Roda checagens para uma seção específica.
    POST /reviews/{id}/sections/{section_id}/run-checks
    """
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review não encontrada")
    
    section = db.query(Section).filter(
        Section.id == section_id,
        Section.review_id == review_id
    ).first()
    if not section:
        raise HTTPException(status_code=404, detail="Seção não encontrada")
    
    # Extrair conteúdo da seção
    try:
        extractor = SectionExtractor(section.url, section.anchor)
        section_data = extractor.extract_all()
    except Exception as e:
        logger.error(f"Erro ao extrair seção {section_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao extrair seção: {e}")
    
    # Rodar checagens
    check_engine = CheckEngine(review.report_year, review.base_year)
    check_results = check_engine.run_all_checks(
        section_data,
        section.url,
        section.anchor or ""
    )
    
    # Salvar check run
    check_run = CheckRun(
        review_id=review_id,
        section_id=section_id,
        mode="section",
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow()
    )
    db.add(check_run)
    db.flush()
    
    # Salvar resultados
    for result in check_results:
        check_result = CheckResult(
            checkrun_id=check_run.id,
            rule=result["rule"],
            severity=result["severity"],
            message=result["message"],
            evidence_json=result.get("evidence")
        )
        db.add(check_result)
    
    db.commit()
    db.refresh(check_run)
    
    logger.info(f"Checagens executadas para seção {section_id}: {len(check_results)} resultados")
    return check_run


@app.get("/reviews/{review_id}/sections/{section_id}/results", response_model=CheckRunResponse)
async def get_section_results(
    review_id: int,
    section_id: int,
    db: Session = Depends(get_db)
):
    """GET /reviews/{id}/sections/{section_id}/results"""
    section = db.query(Section).filter(
        Section.id == section_id,
        Section.review_id == review_id
    ).first()
    if not section:
        raise HTTPException(status_code=404, detail="Seção não encontrada")
    
    # Retornar último check run
    check_run = db.query(CheckRun).filter(
        CheckRun.section_id == section_id
    ).order_by(CheckRun.finished_at.desc()).first()
    
    if not check_run:
        raise HTTPException(status_code=404, detail="Nenhum resultado encontrado")
    
    return check_run


@app.post("/reviews/{review_id}/run-all")
async def run_all_checks(
    review_id: int,
    max_pages: int = 50,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    """
    Roda checagens para todas as páginas (deduplicadas por URL).
    POST /reviews/{id}/run-all?max_pages=50
    """
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review não encontrada")
    
    if background_tasks:
        background_tasks.add_task(
            run_all_checks_task,
            review_id=review_id,
            max_pages=max_pages
        )
    
    return {
        "message": "Checagens iniciadas",
        "review_id": review_id,
        "max_pages": max_pages,
        "status": "processing"
    }


@app.post("/reviews/{review_id}/sections/{section_id}/manual")
async def save_manual_review(
    review_id: int,
    section_id: int,
    manual_data: ManualReviewCreate,
    db: Session = Depends(get_db)
):
    """
    Salva revisão manual para uma seção.
    POST /reviews/{id}/sections/{section_id}/manual
    """
    section = db.query(Section).filter(
        Section.id == section_id,
        Section.review_id == review_id
    ).first()
    if not section:
        raise HTTPException(status_code=404, detail="Seção não encontrada")
    
    # Procura ou cria registro de manual review
    manual = db.query(ManualReview).filter(
        ManualReview.section_id == section_id
    ).first()
    
    if not manual:
        manual = ManualReview(
            review_id=review_id,
            section_id=section_id
        )
        db.add(manual)
    
    manual.items_checked_json = manual_data.items_checked_json
    manual.comments = manual_data.comments
    manual.reviewer = manual_data.reviewer
    manual.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(manual)
    
    logger.info(f"Revisão manual salva para seção {section_id}")
    return manual


@app.get("/reviews/{review_id}/export")
async def export_report(
    review_id: int,
    format: str = "html",
    db: Session = Depends(get_db)
):
    """
    Exporta relatório em HTML ou PDF.
    GET /reviews/{id}/export?format=html
    """
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review não encontrada")
    
    if format == "html":
        filename = ReportGenerator.save_html(db, review_id)
    elif format == "pdf":
        filename = ReportGenerator.save_pdf(db, review_id)
        if not filename:
            raise HTTPException(status_code=500, detail="PDF não pode ser gerado (WeasyPrint não disponível)")
    else:
        raise HTTPException(status_code=400, detail="Formato deve ser 'html' ou 'pdf'")
    
    return {
        "message": "Relatório gerado",
        "filename": filename,
        "download_url": f"/downloads/{filename}"
    }


@app.get("/downloads/{filename}")
async def download_file(filename: str):
    """
    Serve arquivo gerado para download.
    GET /downloads/{filename}
    """
    filepath = EXPORTS_DIR / filename
    
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    
    # Determinar media type
    if filename.endswith(".pdf"):
        media_type = "application/pdf"
    else:
        media_type = "text/html"
    
    return FileResponse(
        path=filepath,
        media_type=media_type,
        filename=filename
    )


@app.get("/")
async def root():
    """Endpoint raiz com informações da API."""
    return {
        "name": "Auditoria Anuário Estatístico UnB",
        "version": "0.1.0",
        "endpoints": [
            "POST /reviews - Criar nova review",
            "GET /reviews/{id} - Obter review",
            "GET /reviews/{id}/sections - Listar seções",
            "POST /reviews/{id}/sections/{section_id}/run-checks - Rodar checagens",
            "GET /reviews/{id}/sections/{section_id}/results - Obter resultados",
            "POST /reviews/{id}/run-all - Rodar checagens para todas as páginas",
            "POST /reviews/{id}/sections/{section_id}/manual - Salvar revisão manual",
            "GET /reviews/{id}/export?format=html - Exportar relatório",
            "GET /downloads/{filename} - Baixar arquivo"
        ]
    }


# ===== TASKS BACKGROUND =====

def extract_toc_task(review_id: int, start_url: str):
    """Task para extrair TOC em background."""
    from app.database import SessionLocal
    db = SessionLocal()
    
    try:
        logger.info(f"Iniciando extração de TOC para review {review_id}")
        
        # Extrair TOC
        extractor = TOCExtractor(start_url)
        sections_data = extractor.extract_toc()
        
        # Salvar seções no banco
        for section_data in sections_data:
            section = Section(
                review_id=review_id,
                title=section_data["title"],
                url=section_data["url"],
                anchor=section_data["anchor"],
                level=section_data["level"],
                is_virtual=False
            )
            db.add(section)
        
        db.commit()
        logger.info(f"TOC extraído: {len(sections_data)} seções")
    
    except Exception as e:
        logger.error(f"Erro ao extrair TOC: {e}")
    
    finally:
        db.close()


def run_all_checks_task(review_id: int, max_pages: int = 50):
    """Task para rodar checagens em todas as páginas."""
    from app.database import SessionLocal
    db = SessionLocal()
    
    try:
        logger.info(f"Iniciando run-all para review {review_id}")
        
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            return
        
        # Obter URLs únicas (deduplicar por URL, ignorar anchors)
        sections = db.query(Section).filter(
            Section.review_id == review_id,
            Section.is_virtual == False
        ).all()
        
        unique_urls = {}
        for section in sections:
            if section.url not in unique_urls:
                unique_urls[section.url] = section
        
        # Limitar a max_pages
        urls_to_check = list(unique_urls.values())[:max_pages]
        
        check_engine = CheckEngine(review.report_year, review.base_year)
        
        for idx, section in enumerate(urls_to_check, 1):
            try:
                logger.info(f"Checando página {idx}/{len(urls_to_check)}: {section.url}")
                
                # Extrair conteúdo
                extractor = SectionExtractor(section.url, None)
                section_data = extractor.extract_all()
                
                # Rodar checagens
                check_results = check_engine.run_all_checks(
                    section_data,
                    section.url,
                    ""
                )
                
                # Salvar check run
                check_run = CheckRun(
                    review_id=review_id,
                    section_id=section.id,
                    mode="page",
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow()
                )
                db.add(check_run)
                db.flush()
                
                for result in check_results:
                    check_result = CheckResult(
                        checkrun_id=check_run.id,
                        rule=result["rule"],
                        severity=result["severity"],
                        message=result["message"],
                        evidence_json=result.get("evidence")
                    )
                    db.add(check_result)
                
                db.commit()
            
            except Exception as e:
                logger.error(f"Erro ao checar {section.url}: {e}")
                continue
        
        logger.info(f"Run-all concluído para review {review_id}")
    
    except Exception as e:
        logger.error(f"Erro em run_all_checks_task: {e}")
    
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
