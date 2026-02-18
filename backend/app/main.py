from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
from collections import Counter

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

def extract_bookdown_tables(html: str) -> list:
    """Extrai estruturas de tabela do Bookdown (listas, parágrafos com dados)"""
    soup = BeautifulSoup(html, 'html.parser')
    
    tables = []
    
    # 1. Extrair listas (principais estruturas de dados)
    ul_lists = soup.find_all('ul', recursive=True)
    
    for ul_idx, ul in enumerate(ul_lists, 1):
        items = ul.find_all('li', recursive=False)  # Diretos, não nested
        
        if len(items) > 2:
            # Verificar se parece ser dados (tem números)
            list_text = ' '.join([li.get_text() for li in items])
            if re.search(r'\d+', list_text):
                table_data = []
                for li in items:
                    # Extrair dados do item
                    text = li.get_text(strip=True)
                    # Dividir por separadores comuns
                    cells = re.split(r'[-–—:\s{2,}]', text)
                    cells = [c.strip() for c in cells if c.strip()]
                    if cells:
                        table_data.append(cells)
                
                if table_data:
                    tables.append({
                        "type": "LISTA",
                        "number": ul_idx,
                        "name": f"Lista {ul_idx} (Dados)",
                        "data": table_data,
                        "row_count": len(table_data),
                        "col_count": len(table_data[0]) if table_data else 0
                    })
    
    # 2. Extrair blocos de parágrafos com números
    paragraphs = soup.find_all('p')
    
    para_blocks = []
    for p in paragraphs:
        text = p.get_text(strip=True)
        if re.search(r'\d+', text):
            para_blocks.append(text)
    
    # Agrupar parágrafos consecutivos com dados
    grouped_blocks = []
    current_group = []
    
    for block in para_blocks:
        if re.search(r'\d+', block):
            current_group.append(block)
        else:
            if len(current_group) > 2:
                grouped_blocks.append(current_group)
            current_group = []
    
    if current_group:
        grouped_blocks.append(current_group)
    
    # Processar grupos como pseudo-tabelas
    for block_idx, group in enumerate(grouped_blocks[:5], 1):  # Limitar a 5
        if len(group) > 2:
            table_data = []
            for line in group:
                # Tentar extrair números e separadores
                parts = re.split(r'[-–—:|,]', line)
                parts = [p.strip() for p in parts if p.strip()]
                if parts:
                    table_data.append(parts)
            
            if table_data:
                tables.append({
                    "type": "PARAGRAFO",
                    "number": block_idx,
                    "name": f"Bloco de Dados {block_idx}",
                    "data": table_data,
                    "row_count": len(table_data),
                    "col_count": max(len(row) for row in table_data) if table_data else 0
                })
    
    # 3. Procurar por seções estruturadas (h2 seguido de conteúdo)
    h2_sections = soup.find_all('h2')
    
    for h2_idx, h2 in enumerate(h2_sections, 1):
        # Pegar conteúdo até próximo h2
        section_text = h2.get_text(strip=True)
        
        # Procurar por padrão: "nome: valor"
        next_elem = h2.find_next(['p', 'ul', 'div'])
        if next_elem:
            content = next_elem.get_text(strip=True)
            
            # Se tem números, pode ser tabela
            if re.search(r'\d+', content):
                # Extrair pares chave:valor
                pairs = re.findall(r'([^:]+):\s*(\d+[.,\d]*)', content)
                if len(pairs) > 2:
                    table_data = [list(pair) for pair in pairs]
                    tables.append({
                        "type": "SECAO",
                        "number": h2_idx,
                        "name": f"Seção: {section_text[:40]}",
                        "data": table_data,
                        "row_count": len(table_data),
                        "col_count": 2
                    })
    
    return tables

def analyze_bookdown_data(tables: list) -> list:
    """Analisa dados extraídos do Bookdown"""
    issues = []
    
    if not tables:
        issues.append({
            "severity": "INFO",
            "table": "Documento",
            "issue": "Estrutura Bookdown detectada",
            "detail": "Documento é um Bookdown/GitBook. Estrutura: listas, parágrafos e seções com dados formatados.",
            "recommendation": "Analisando estruturas de dados formatadas..."
        })
        return issues
    
    # Analisar cada tabela
    for table in tables:
        data = table["data"]
        table_name = table["name"]
        
        # 1. Verificar células vazias
        total_cells = sum(len(row) for row in data)
        empty_cells = sum(1 for row in data for cell in row if not cell or not cell.strip())
        
        if total_cells > 0:
            empty_pct = (empty_cells / total_cells * 100)
            if empty_pct > 20:
                issues.append({
                    "severity": "WARN",
                    "table": table_name,
                    "issue": f"{empty_pct:.1f}% de células vazias",
                    "detail": f"{empty_cells} de {total_cells} células",
                    "recommendation": "Verificar dados faltantes"
                })
        
        # 2. Valores duplicados
        all_values = []
        for row in data:
            for cell in row:
                if cell and cell.strip() and any(c.isdigit() for c in cell):
                    all_values.append(cell.strip())
        
        if all_values:
            value_counts = Counter(all_values)
            dups = {k: v for k, v in value_counts.items() if v > 2}
            
            if dups:
                for val, count in list(dups.items())[:2]:
                    issues.append({
                        "severity": "WARN",
                        "table": table_name,
                        "issue": f"Valor '{val}' repetido {count} vezes",
                        "detail": f"Valor aparece múltiplas vezes",
                        "recommendation": "Verificar se é cópia ou dado genuíno"
                    })
        
        # 3. Inconsistência de estrutura
        col_counts = [len(row) for row in data]
        if len(set(col_counts)) > 1:
            issues.append({
                "severity": "WARN",
                "table": table_name,
                "issue": f"Estrutura inconsistente",
                "detail": f"Linhas têm {min(col_counts)}-{max(col_counts)} colunas",
                "recommendation": "Padronizar número de colunas"
            })
        
        # 4. Procurar por valores suspeitos
        for row_idx, row in enumerate(data):
            row_text = ' '.join(row)
            
            # Verificar se parece ser um total
            if any(x in row_text.lower() for x in ['total', 'soma', 'subtotal']):
                numbers = []
                for cell in row:
                    # Extrair números
                    nums = re.findall(r'\d+(?:[.,]\d+)?', cell)
                    for n in nums:
                        try:
                            num = float(n.replace(',', '.'))
                            numbers.append(num)
                        except:
                            pass
                
                if len(numbers) >= 3:
                    parts = numbers[:-1]
                    total = numbers[-1]
                    calculated = sum(parts)
                    
                    if calculated > 0 and abs(calculated - total) > 0.01:
                        issues.append({
                            "severity": "FAIL",
                            "table": table_name,
                            "issue": f"Erro na soma (linha {row_idx+1})",
                            "detail": f"Soma esperada: {calculated:.0f}, mas registrado: {total:.0f}",
                            "recommendation": "Recalcular totais"
                        })
    
    return issues

def run_bookdown_audit(html: str, report_year: int, base_year: int) -> list:
    """Auditoria completa para Bookdown"""
    issues = []
    
    # Extrair estruturas
    tables = extract_bookdown_tables(html)
    
    # Analisar
    data_issues = analyze_bookdown_data(tables)
    issues.extend(data_issues)
    
    # Verificações globais
    year_str = str(report_year)
    if year_str in html:
        count = html.count(year_str)
        issues.append({
            "severity": "PASS",
            "table": "Metadados",
            "issue": f"Ano {year_str} presente",
            "detail": f"Ano aparece {count} vez(es) no documento",
            "recommendation": "OK"
        })
    else:
        issues.append({
            "severity": "WARN",
            "table": "Metadados",
            "issue": f"Ano {year_str} não encontrado",
            "detail": "Referência ao ano não foi localizada",
            "recommendation": f"Adicionar ano {year_str}"
        })
    
    # Formatação
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text()
    
    comma_nums = len(re.findall(r'\d{1,3},\d{2,}', text))
    dot_nums = len(re.findall(r'\d{1,3}\.\d{2,}', text))
    
    if comma_nums > 0 and dot_nums > 0:
        issues.append({
            "severity": "WARN",
            "table": "Formatação",
            "issue": "Mistura de separadores decimais",
            "detail": f"{comma_nums} com vírgula, {dot_nums} com ponto",
            "recommendation": "Padronizar separador decimal"
        })
    
    # Se não houver problemas, adicionar confirmação
    if len([i for i in issues if i["severity"] in ["FAIL", "WARN"]]) == 0:
        issues.append({
            "severity": "PASS",
            "table": "Análise",
            "issue": "Auditoria concluída com sucesso",
            "detail": f"Analisadas {len(tables)} estrutura(s) de dados. Nenhum problema crítico detectado.",
            "recommendation": "Documento está adequado"
        })
    
    return issues

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
        .issue-item {
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            border-left: 5px solid;
        }
        .issue-item.pass { background: #e8f5e9; border-left-color: #4caf50; }
        .issue-item.warn { background: #fff3e0; border-left-color: #ff9800; }
        .issue-item.fail { background: #ffebee; border-left-color: #f44336; }
        .issue-item.info { background: #e3f2fd; border-left-color: #2196f3; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; color: white; }
        .badge-pass { background: #4caf50; }
        .badge-warn { background: #ff9800; }
        .badge-fail { background: #f44336; }
        .badge-info { background: #2196f3; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Auditoria - Anuário UnB</h1>
        <p class="subtitle">Análise de Bookdown/GitBook - Detecção de inconsistências</p>

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
            <button onclick="audit()">🔍 Executar Auditoria</button>
        </div>

        <div id="loading">
            <div class="spinner"></div>
            <p>Analisando Bookdown...</p>
        </div>

        <div id="results" class="results">
            <div class="stats" id="stats"></div>
            <h2 style="color: #003366; margin-bottom: 20px;">Inconsistências Detectadas</h2>
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
                    <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                        <strong>${i.table}</strong>
                        <span class="badge badge-${i.severity.toLowerCase()}">${i.severity}</span>
                    </div>
                    <div style="color: #333; font-weight: 500; margin-bottom: 8px;">${i.issue}</div>
                    <div style="color: #555; font-size: 14px; margin-bottom: 10px;">${i.detail}</div>
                    <div style="color: #666; font-style: italic; font-size: 13px; padding: 10px; background: rgba(0,0,0,0.03); border-radius: 4px;">
                        💡 ${i.recommendation}
                    </div>
                </div>
            `).join('');

            document.getElementById('loading').style.display = 'none';
            document.getElementById('results').style.display = 'block';
        }
    </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def serve():
    return HTML

@app.post("/audit")
def audit(req: AuditRequest):
    try:
        resp = requests.get(req.url, timeout=20)
        html = resp.text
        
        issues = run_bookdown_audit(html, req.report_year, req.base_year)
        
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