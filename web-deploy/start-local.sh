#!/bin/bash

# Script para rodar a aplicação web localmente com Docker

set -e

# Cores
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "=================================================="
echo "  Auditoria Anuário UnB - Aplicação Web"
echo "=================================================="
echo -e "${NC}"

# Verificar Docker
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}⚠ Docker não encontrado!${NC}"
    echo "Instale Docker em: https://docker.com"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}⚠ Docker Compose não encontrado!${NC}"
    echo "Instale Docker Compose em: https://docs.docker.com/compose/install/"
    exit 1
fi

echo -e "${GREEN}✓${NC} Docker encontrado"
echo -e "${GREEN}✓${NC} Docker Compose encontrado"

# Verificar se backend existe
if [ ! -d "../backend" ]; then
    echo -e "${YELLOW}⚠ Backend não encontrado em ../backend${NC}"
    echo "Certifique-se que os arquivos estão organizados:"
    echo "  backend/"
    echo "  web-deploy/"
    exit 1
fi

echo -e "\n${BLUE}=================================================="
echo "  Iniciando aplicação..."
echo "==================================================${NC}\n"

# Iniciar com docker-compose
docker-compose up --build

# Instruções finais
echo -e "\n${GREEN}"
echo "=================================================="
echo "  ✓ Aplicação iniciada com sucesso!"
echo "=================================================="
echo -e "${NC}"
echo -e "Acesse: ${BLUE}http://localhost${NC}"
echo ""
echo "Para parar: Ctrl+C ou em outro terminal execute:"
echo "  docker-compose down"
echo ""
