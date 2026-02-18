import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Banco de dados
DATABASE_URL = "sqlite:///./anuario_audit.db"
DATABASE_PATH = BASE_DIR / "anuario_audit.db"

# Diretórios
EXPORTS_DIR = BASE_DIR / "exports"
TEMPLATES_DIR = BASE_DIR / "templates"
DOWNLOADS_DIR = EXPORTS_DIR / "downloads"

# Criar diretórios se não existirem
EXPORTS_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Configurações de scraping
REQUEST_TIMEOUT = 30
MAX_PAGES_DEFAULT = 50
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Configurações de checagem
DECIMAL_SEPARATOR_WARN_THRESHOLD = 0.5  # % de células com ponto decimal
