# DEPENDÊNCIAS DO SISTEMA

Este projeto requer Python 3.11+ e algumas dependências do sistema.

## Linux (Ubuntu/Debian)

```bash
# Atualizar repositórios
sudo apt-get update

# Python 3.11+
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev

# Ferramentas de build
sudo apt-get install -y build-essential

# Para WeasyPrint (PDF):
sudo apt-get install -y \
    libpango-1.0-0 \
    libpango-gobject-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libcairo2 \
    libcairo2-dev \
    fonts-liberation

# SQLite3 (opcional, para inspecionar banco manualmente)
sudo apt-get install -y sqlite3
```

## macOS

```bash
# Homebrew (https://brew.sh)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python 3.11+
brew install python@3.11

# Dependências para WeasyPrint
brew install cairo pango gdk-pixbuf libffi

# SQLite3
brew install sqlite
```

## Windows

### Python 3.11+
- Baixar em: https://www.python.org/downloads/
- Verificar: "Add Python to PATH"
- Instalar

### Visual C++ Build Tools (necessário para compilar pacotes)
- Baixar em: https://visualstudio.microsoft.com/visual-cpp-build-tools/
- Seguir instalação padrão

### Git Bash (recomendado para terminal)
- Baixar em: https://git-scm.com/download/win
- Instalar com opções padrão

### SQLite3 (opcional)
- Já incluído em Python ou: https://www.sqlite.org/download.html

### Configuração de Path (se necessário)
```cmd
# Adicionar Python à variável PATH
setx PATH "%PATH%;C:\Users\[SEU_USUARIO]\AppData\Local\Programs\Python\Python311"
```

## Verificar Instalação

```bash
# Python
python3 --version
# Esperado: Python 3.11.x ou superior

# pip
pip --version
# Esperado: pip 24.x.x

# sqlite3 (opcional)
sqlite3 --version
# Esperado: 3.x.x
```

## Instalar Dependências Python

```bash
cd backend

# Criar virtual environment
python3 -m venv venv

# Ativar (Linux/macOS)
source venv/bin/activate

# Ativar (Windows)
venv\Scripts\activate

# Instalar dependências
pip install -r requirements.txt
```

## Troubleshooting

### Erro: "No module named '_sqlite3'"
**Solução:** Reinstalar Python com suporte a sqlite3

### Erro: "libcairo.so.2: cannot open shared object"
**Solução (Linux):**
```bash
sudo apt-get install -y libcairo2 libcairo2-dev
```

### Erro: "pip is not recognized"
**Solução (Windows):**
```cmd
python -m pip install --upgrade pip
```

### WeasyPrint não funciona
**Nota:** É opcional. HTML será gerado mesmo sem WeasyPrint.

Para forçar funcionar:
```bash
pip install --upgrade weasyprint

# Se persistir erro, reinstalar com force-reinstall
pip install --force-reinstall --no-cache-dir weasyprint
```

## Requisitos Mínimos Recomendados

| Componente | Mínimo | Recomendado |
|------------|--------|-------------|
| Python | 3.11 | 3.11+ |
| RAM | 512MB | 2GB |
| Disco | 200MB | 500MB |
| Conexão | Necessária | 10Mbps+ |

## Próximo Passo

Após instalar dependências do sistema, execute:
```bash
bash run.sh
```
