from jinja2 import Template
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging
from sqlalchemy.orm import Session
from app.models import Review, Section, CheckRun, CheckResult, ManualReview
from app.config import EXPORTS_DIR, TEMPLATES_DIR

logger = logging.getLogger(__name__)

# Template HTML base
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria - {{ review.start_url }}</title>
    <style>
        * { margin: 0; padding: 0; }
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        header h1 { font-size: 2em; margin-bottom: 10px; }
        header p { font-size: 0.95em; opacity: 0.9; }
        
        .summary {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            margin-bottom: 30px;
        }
        .summary-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            text-align: center;
            border-left: 4px solid #667eea;
        }
        .summary-card.fail { border-left-color: #e74c3c; }
        .summary-card.warn { border-left-color: #f39c12; }
        .summary-card.pass { border-left-color: #27ae60; }
        .summary-card h3 { font-size: 2em; margin: 10px 0; }
        .summary-card p { font-size: 0.9em; color: #666; }
        
        .section-block {
            background: white;
            margin-bottom: 30px;
            padding: 25px;
            border-radius: 8px;
            border-left: 4px solid #667eea;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .section-block h2 {
            font-size: 1.5em;
            margin-bottom: 15px;
            color: #667eea;
        }
        .section-info {
            background: #f8f9fa;
            padding: 10px;
            margin-bottom: 15px;
            border-radius: 5px;
            font-size: 0.9em;
            color: #666;
        }
        
        .checks {
            margin-top: 15px;
        }
        .check-result {
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 5px;
            border-left: 4px solid;
        }
        .check-result.PASS {
            background: #d4edda;
            border-color: #28a745;
            color: #155724;
        }
        .check-result.WARN {
            background: #fff3cd;
            border-color: #ffc107;
            color: #856404;
        }
        .check-result.FAIL {
            background: #f8d7da;
            border-color: #dc3545;
            color: #721c24;
        }
        .check-result strong { display: block; margin-bottom: 5px; }
        .check-result .rule { font-size: 0.85em; opacity: 0.8; margin-top: 5px; }
        
        .evidence {
            background: #f0f0f0;
            padding: 10px;
            margin-top: 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-family: monospace;
            word-break: break-all;
            max-height: 150px;
            overflow-y: auto;
        }
        
        .no-results {
            background: #e8f5e9;
            padding: 20px;
            border-radius: 5px;
            text-align: center;
            color: #27ae60;
        }
        
        .manual-review {
            background: #e3f2fd;
            padding: 15px;
            margin-top: 15px;
            border-radius: 5px;
            border-left: 4px solid #2196F3;
        }
        
        footer {
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            text-align: center;
            color: #666;
            font-size: 0.9em;
        }
        
        @media print {
            body { background: white; }
            .section-block { page-break-inside: avoid; }
        }
    </style>
</head>
<body>
    <header>
        <h1>📋 Auditoria do Anuário Estatístico</h1>
        <p><strong>URL:</strong> {{ review.start_url }}</p>
        <p><strong>Ano de Relatório:</strong> {{ review.report_year }} | 
           <strong>Ano Base:</strong> {{ review.base_year }} | 
           <strong>Gerado em:</strong> {{ generated_at }}</p>
    </header>

    <!-- SUMÁRIO EXECUTIVO -->
    <div class="summary">
        <div class="summary-card fail">
            <p>Erros (FAIL)</p>
            <h3>{{ stats.fail }}</h3>
        </div>
        <div class="summary-card warn">
            <p>Avisos (WARN)</p>
            <h3>{{ stats.warn }}</h3>
        </div>
        <div class="summary-card pass">
            <p>Aprovado (PASS)</p>
            <h3>{{ stats.pass }}</h3>
        </div>
        <div class="summary-card">
            <p>Total de Seções</p>
            <h3>{{ sections|length }}</h3>
        </div>
    </div>

    <!-- DETALHAMENTO POR SEÇÃO -->
    {% for section in sections %}
    <div class="section-block">
        <h2>{{ section.title }}</h2>
        <div class="section-info">
            <strong>URL:</strong> <code>{{ section.url }}</code>
            {% if section.anchor %}<br><strong>Âncora:</strong> <code>#{{ section.anchor }}</code>{% endif %}
            <br><strong>Nível:</strong> {{ section.level }}
        </div>
        
        {% if section.check_results %}
            <div class="checks">
                {% for result in section.check_results %}
                <div class="check-result {{ result.severity }}">
                    <strong>[{{ result.severity }}] {{ result.rule }}</strong>
                    <p>{{ result.message }}</p>
                    {% if result.evidence_json %}
                    <div class="evidence">
                        <strong>Evidência:</strong><br>
                        {{ result.evidence_json|tojson(indent=2) }}
                    </div>
                    {% endif %}
                    <div class="rule">Regra: {{ result.rule }}</div>
                </div>
                {% endfor %}
            </div>
        {% else %}
            <div class="no-results">✓ Nenhuma falha detectada</div>
        {% endif %}
        
        {% if section.manual_review %}
        <div class="manual-review">
            <strong>Revisão Manual</strong><br>
            <p><strong>Revisor:</strong> {{ section.manual_review.reviewer }}</p>
            <p><strong>Comentários:</strong> {{ section.manual_review.comments }}</p>
            <p><strong>Atualizado em:</strong> {{ section.manual_review.updated_at }}</p>
        </div>
        {% endif %}
    </div>
    {% endfor %}

    <footer>
        <p>Relatório gerado automaticamente pelo Sistema de Auditoria do Anuário Estatístico da UnB</p>
        <p>{{ generated_at }}</p>
    </footer>
</body>
</html>
"""


class ReportGenerator:
    """Gera relatórios HTML e PDF a partir dos dados armazenados."""
    
    @staticmethod
    def generate_html(db: Session, review_id: int) -> str:
        """Gera relatório HTML para uma review."""
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            raise ValueError(f"Review {review_id} não encontrada")
        
        # Coleta seções com seus resultados
        sections_data = []
        stats = {"pass": 0, "warn": 0, "fail": 0}
        
        for section in review.sections:
            section_info = {
                "title": section.title,
                "url": section.url,
                "anchor": section.anchor,
                "level": section.level,
                "check_results": [],
                "manual_review": None,
            }
            
            # Coleta resultados de checagem
            for check_run in section.check_runs:
                for result in check_run.results:
                    section_info["check_results"].append({
                        "rule": result.rule,
                        "severity": result.severity,
                        "message": result.message,
                        "evidence_json": result.evidence_json,
                    })
                    stats[result.severity.lower()] += 1
            
            # Coleta revisão manual se existir
            manual = db.query(ManualReview).filter(
                ManualReview.section_id == section.id
            ).first()
            if manual:
                section_info["manual_review"] = {
                    "reviewer": manual.reviewer,
                    "comments": manual.comments,
                    "items_checked": manual.items_checked_json,
                    "updated_at": manual.updated_at.strftime("%d/%m/%Y %H:%M"),
                }
            
            sections_data.append(section_info)
        
        # Renderizar template
        template = Template(HTML_TEMPLATE)
        html = template.render(
            review=review,
            sections=sections_data,
            stats=stats,
            generated_at=datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        )
        
        return html
    
    @staticmethod
    def save_html(db: Session, review_id: int, filename: Optional[str] = None) -> str:
        """Salva relatório HTML em arquivo."""
        if not filename:
            filename = f"report_review_{review_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        
        html = ReportGenerator.generate_html(db, review_id)
        
        filepath = EXPORTS_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        
        logger.info(f"Relatório HTML salvo em {filepath}")
        return filename
    
    @staticmethod
    def save_pdf(db: Session, review_id: int, filename: Optional[str] = None) -> Optional[str]:
        """Salva relatório em PDF (se WeasyPrint disponível)."""
        try:
            from weasyprint import HTML as WeasyHTML
        except ImportError:
            logger.warning("WeasyPrint não instalado. PDF não será gerado.")
            return None
        
        if not filename:
            filename = f"report_review_{review_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        html = ReportGenerator.generate_html(db, review_id)
        
        filepath = EXPORTS_DIR / filename
        try:
            WeasyHTML(string=html).write_pdf(str(filepath))
            logger.info(f"Relatório PDF salvo em {filepath}")
            return filename
        except Exception as e:
            logger.error(f"Erro ao gerar PDF: {e}")
            return None
