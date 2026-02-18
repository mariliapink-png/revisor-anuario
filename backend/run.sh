#!/bin/bash

# Script para iniciar o backend da Auditoria do Anuário UnB

set -e

# Cores
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "=================================================="
echo "  Auditoria Anuário Estatístico UnB - Backend"
echo "=================================================="
echo -e "${NC}"

# Verificar Python
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}⚠ Python 3 não encontrado${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo -e "${GREEN}✓${NC} Python $PYTHON_VERSION encontrado"

# Verificar/criar venv
if [ ! -d "venv" ]; then
    echo -e "\n${YELLOW}Criando virtual environment...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✓${NC} Virtual environment criado"
fi

# Ativar venv
echo -e "\n${YELLOW}Ativando virtual environment...${NC}"
source venv/bin/activate
echo -e "${GREEN}✓${NC} Ambiente ativado"

# Instalar dependências
echo -e "\n${YELLOW}Instalando dependências...${NC}"
pip install -q -r requirements.txt
echo -e "${GREEN}✓${NC} Dependências instaladas"

# Iniciar servidor
echo -e "\n${BLUE}=================================================="
echo "  Iniciando servidor FastAPI"
echo "=================================================="
echo -e "${NC}"
echo -e "${GREEN}✓${NC} Servidor disponível em: http://localhost:8000"
echo -e "${GREEN}✓${NC} Documentação: http://localhost:8000/docs"
echo -e "${GREEN}✓${NC} Banco de dados: ./anuario_audit.db"
echo ""
echo "Para testar, em outro terminal:"
echo "  bash test.sh"
echo ""
echo "Ou execute um curl:"
echo "  curl http://localhost:8000/"
echo ""
echo -e "${YELLOW}Pressione Ctrl+C para parar${NC}"
echo -e "${BLUE}==================================================${NC}\n"

# Iniciar uvicorn
cd app
uvicorn main:app --reload --host 0.0.0.0 --port 8000
