from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
import requests
from bs4 import BeautifulSoup
import re
from collections import defaultdict

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

class AuditIssue(BaseModel):
    severity: str
    table: str
    issue: str
    detail: str
    recommendation: str

def extract_table_data(table) -> Dict[str, Any]:
    """Extrai dados estruturados de uma tabela"""
    rows = table.find_all('tr')
    data = []
    headers = []
    
    for row_idx, row in enumerate(rows):
        cells = row.find_all(['td', 'th'])
        row_data = []
        
        for cell in cells:
            text = cell.get_text(strip=True)
            row_data.append(text)
        
        if row_idx == 0 and all(cell.name == 'th' for cell in cells):
            headers = row_data
        else:
            data.append(row_data)
    
    return {
        "headers": headers,
        "data": data,
        "rows": len(data),
        "cols": len(headers) if headers else (len(data[0]) if data else 0)
    }

def extract_numbers(text: str) -> List[float]:
    """Extrai números de um texto"""
    patterns = [
        r'\d+\.\d+',  # 1.234
        r'\d+,\d+',   # 1,234
        r'\d+',       # 1234
    ]
    
    numbers = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                num_str = match.group()
                # Converter para número
                num_str = num_str.replace(',', '.')
                num = float(num_str)
                if num not in numbers:
                    numbers.append(num)
            except:
                pass
    
    return numbers

def run_detailed_audit(html: str, report_year: int, base_year: int) -> List[Dict]:
    """Faz auditoria detalhada com análise real de tabelas"""
    issues = []
    
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    
    if len(tables) == 0:
        return [{
            "severity": "FAIL",
            "table": "Documento",
            "issue": "Nenhuma tabela encontrada",
            "detail": "O documento não contém dados tabulares",
            "recommendation": "Adicionar tabelas com dados"
        }]
    
    # Analisar cada tabela
    for table_idx, table in enumerate(tables, 1):
        table_name = f"Tabela {table_idx}"
        
        # Tentar pegar título/legenda
        caption = table.find('caption')
        if caption:
            table_name = f"Tabela {table_idx} - {caption.get_text(strip=True)[:50]}"
        
        table_data = extract_table_data(table)
        rows = table_data["data"]
        cols = table_data["cols"]
        headers = table_data["headers"]
        
        # VERIFICAÇÃO 1: Células vazias e padrão
        empty_cells = sum(1 for row in rows for cell in row if not cell.strip())
        total_cells = sum(len(row) for row in rows)
        
        if empty_cells > 0:
            empty_pct = (empty_cells / total_cells * 100) if total_cells > 0 else 0
            
            if empty_pct > 30:
                issues.append({
                    "severity": "FAIL",
                    "table": table_name,
                    "issue": f"{empty_pct:.1f}% de células vazias ({empty_cells}/{total_cells})",
                    "detail": f"Muitas células sem preenchimento. Padrão inconsistente de dados.",
                    "recommendation": "Preencher células vazias ou padronizar com '0' ou 'N/A'"
                })
            elif empty_pct > 10:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"{empty_pct:.1f}% de células vazias",
                    "detail": f"{empty_cells} células sem preenchimento em {total_cells} totais.",
                    "recommendation": "Verificar se é intencional ou erro de digitação"
                })
        
        # VERIFICAÇÃO 2: Inconsistência de estrutura
        col_counts = [len(row) for row in rows]
        if len(set(col_counts)) > 1:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": "Número de colunas inconsistente",
                "detail": f"Linhas têm {min(col_counts)}-{max(col_counts)} colunas. Padrão: {col_counts}",
                "recommendation": "Padronizar número de colunas por linha"
            })
        
        # VERIFICAÇÃO 3: Linhas duplicadas
        row_signatures = defaultdict(int)
        for row in rows:
            sig = tuple(row[:3])  # Usar primeiras 3 colunas como assinatura
            row_signatures[sig] += 1
        
        duplicates = {k: v for k, v in row_signatures.items() if v > 1}
        if duplicates:
            for sig, count in list(duplicates.items())[:3]:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"Linha duplicada {count} vezes",
                    "detail": f"Padrão: {' | '.join(str(s)[:30] for s in sig)}",
                    "recommendation": "Verificar se é duplicação acidental"
                })
        
        # VERIFICAÇÃO 4: Análise numérica
        all_numbers = []
        for row in rows:
            for cell in row:
                nums = extract_numbers(cell)
                all_numbers.extend(nums)
        
        if all_numbers:
            # Valores muito grandes repetidos
            from collections import Counter
            num_counts = Counter(all_numbers)
            repeated = {k: v for k, v in num_counts.items() if v > 2}
            
            if repeated:
                for num, count in list(repeated.items())[:2]:
                    issues.append({
                        "severity": "WARN",
                        "table": table_name,
                        "issue": f"Valor {num} repetido {count} vezes",
                        "detail": f"O valor {num} aparece em múltiplas células. Pode indicar dado copiado.",
                        "recommendation": "Verificar se é valor genuíno ou cópia acidental"
                    })
            
            # Valores suspeitos
            sorted_nums = sorted(all_numbers)
            for i in range(len(sorted_nums) - 1):
                if sorted_nums[i] > 0 and sorted_nums[i+1] > 0:
                    ratio = sorted_nums[i+1] / sorted_nums[i]
                    if 0.5 > ratio or ratio > 2:
                        # Variação grande
                        pass
        
        # VERIFICAÇÃO 5: Totais e somas
        for row_idx, row in enumerate(rows):
            row_text = ' '.join(row)
            
            # Se parece ser uma linha de "Total"
            if any(x in row_text.lower() for x in ['total', 'soma', 'subtotal']):
                # Tentar verificar soma
                numbers = extract_numbers(row_text)
                
                if len(numbers) >= 3:
                    # Assumir último é o total
                    parts = numbers[:-1]
                    total = numbers[-1]
                    calculated = sum(parts)
                    
                    if calculated > 0 and abs(calculated - total) > 0.01:
                        issues.append({
                            "severity": "FAIL",
                            "table": table_name,
                            "issue": f"Erro de soma na linha {row_idx+1}",
                            "detail": f"Soma dos valores: {calculated}, mas total registrado: {total}. Diferença: {abs(calculated - total)}",
                            "recommendation": "Recalcular e verificar a soma"
                        })
        
        # VERIFICAÇÃO 6: Dados faltantes em padrão esperado
        if len(rows) > 3:
            # Verificar se primeiro elemento (índice ou ID) está preenchido
            first_col_filled = sum(1 for row in rows if row and row[0].strip())
            if first_col_filled < len(rows) * 0.8:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"Primeira coluna {(1 - first_col_filled/len(rows))*100:.1f}% vazia",
                    "detail": "Possível falta de identificadores de linha",
                    "recommendation": "Verificar se é uma coluna de índice/identificação"
                })
        
        # VERIFICAÇÃO 7: Valores negativos suspeitos
        negative_cells = []
        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                if '-' in cell and any(c.isdigit() for c in cell):
                    numbers = extract_numbers(cell)
                    if any(n < 0 for n in numbers):
                        negative_cells.append((row_idx, col_idx, cell))
        
        if negative_cells:
            for row_idx, col_idx, cell in negative_cells[:2]:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"Valor negativo na linha {row_idx+1}, coluna {col_idx+1}",
                    "detail": f"Valor: {cell}. Verificar se é intencional.",
                    "recommendation": "Confirmar se valores negativos são esperados nesta tabela"
                })
    
    # VERIFICAÇÕES GLOBAIS
    
    # Verificar ano
    year_str = str(report_year)
    if year_str not in html:
        issues.append({
            "severity": "FAIL",
            "table": "Documento Geral",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "O ano de referência não aparece no documento",
            "recommendation": f"Incluir ano {year_str} no documento"
        })
    
    # Verificar formatação consistente
    comma_nums = len(re.findall(r'\d{1,3},\d{2,}', html))
    dot_nums = len(re.findall(r'\d{1,3}\.\d{2,}', html))
    
    if comma_nums > 0 and dot_nums > 0:
        issues.append({
            "severity": "WARN",
            "table": "Documento Geral",
            "issue": "Formatação numérica inconsistente",
            "detail": f"Documento usa {comma_nums} números com vírgula e {dot_nums} com ponto",
            "recommendation": "Padronizar separador decimal em todo documento"
        })
    
    # Se não houver problemas, retornar OK
    if not issues:
        issues.append({
            "severity": "PASS",
            "table": "Documento Completo",
            "issue": "Auditoria concluída - Sem problemas críticos",
            "detail": "Nenhuma inconsistência foi detectada na auditoria.",
            "recommendation": "Continuar monitorando atualizações"
        })
    
    return issues

# ===== HTML =====
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auditoria Anuário UnB</title>
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
            max-width: 1100px;
            margin: 0 auto;
            padding: 40px;
        }
        h1 { color: #003366; margin-bottom: 10px; font-size: 28px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #003366; font-weight: 600; margin-bottom: 8px; }
        input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; }
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
        button:hover { transform: translateY(-2px); }
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
        .issue-item {
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            border-left: 5px solid;
        }
        .issue-item.pass {
            background: #e8f5e9;
            border-left-color: #4caf50;
        }
        .issue-item.warn {
            background: #fff3e0;
            border-left-color: #ff9800;
        }
        .issue-item.fail {
            background: #ffebee;
            border-left-color: #f44336;
        }
        .issue-title {
            font-weight: 600;
            color: #333;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .issue-table {
            color: #666;
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 10px;
            padding: 5px 10px;
            background: rgba(0,0,0,0.05);
            border-radius: 4px;
            display: inline-block;
        }
        .issue-detail {
            color: #555;
            font-size: 14px;
            line-height: 1.6;
            margin-bottom: 12px;
        }
        .issue-recommendation {
            color: #666;
            font-size: 13px;
            font-style: italic;
            padding: 10px;
            background: rgba(0,0,0,0.03);
            border-radius: 4px;
            border-left: 3px solid;
        }
        .issue-item.pass .issue-recommendation { border-left-color: #4caf50; }
        .issue-item.warn .issue-recommendation { border-left-color: #ff9800; }
        .issue-item.fail .issue-recommendation { border-left-color: #f44336; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        .badge-pass { background: #4caf50; color: white; }
        .badge-warn { background: #ff9800; color: white; }
        .badge-fail { background: #f44336; color: white; }
        .report-header { color: #003366; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria Detalhada - Anuário UnB</h1>
        <p class="subtitle">Análise completa de inconsistências em tabelas e dados</p>

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
            <button onclick="audit()">🔍 Executar Auditoria Completa</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Analisando documento e tabelas...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 class="report-header">Discrepâncias que merecem atenção:</h2>
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
                showResults(data.issues);
            } catch (e) {
                alert('Erro: ' + e.message);
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
            }
        }

        function showResults(issues) {
            if (!issues || issues.length === 0) {
                alert('Sem resultados');
                document.getElementById('form').style.display = 'block';
                document.getElementById('loading').style.display = 'none';
                return;
            }

            const pass = issues.filter(i => i.severity === 'PASS').length;
            const warn = issues.filter(i => i.severity === 'WARN').length;
            const fail = issues.filter(i => i.severity === 'FAIL').length;

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

            document.getElementById('content').innerHTML = issues.map(i => `
                <div class="issue-item ${i.severity.toLowerCase()}">
                    <div class="issue-title">
                        <span><span class="issue-table">${i.table}</span></span>
                        <span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span>
                    </div>
                    <div style="color: #333; font-weight: 500; margin-bottom: 8px;">${i.issue}</div>
                    <div class="issue-detail">${i.detail}</div>
                    <div class="issue-recommendation">💡 ${i.recommendation}</div>
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
        resp = requests.get(req.url, timeout=20)
        html = resp.text
        
        issues = run_detailed_audit(html, req.report_year, req.base_year)
        
        return {
            "status": "ok",
            "issues": issues
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)