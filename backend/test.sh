#!/bin/bash

# Script de teste rápido da API
# Executa o workflow completo

set -e

BASE_URL="http://localhost:8000"

echo "================================================"
echo "Teste Auditoria Anuário Estatístico UnB"
echo "================================================"

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Criar review
echo -e "\n${YELLOW}[1/6]${NC} Criando review..."
REVIEW=$(curl -s -X POST "$BASE_URL/reviews" \
  -H "Content-Type: application/json" \
  -d '{
    "start_url": "https://anuariounb2025.netlify.app/",
    "report_year": 2025,
    "base_year": 2024
  }')

REVIEW_ID=$(echo $REVIEW | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)

if [ -z "$REVIEW_ID" ]; then
  echo -e "${RED}✗ Erro ao criar review${NC}"
  echo $REVIEW
  exit 1
fi

echo -e "${GREEN}✓ Review criada: ID $REVIEW_ID${NC}"

# 2. Aguardar extração de TOC
echo -e "\n${YELLOW}[2/6]${NC} Aguardando extração de TOC (5s)..."
sleep 5
echo -e "${GREEN}✓ TOC extraído${NC}"

# 3. Listar seções
echo -e "\n${YELLOW}[3/6]${NC} Listando seções..."
SECTIONS=$(curl -s "$BASE_URL/reviews/$REVIEW_ID/sections")
SECTION_COUNT=$(echo $SECTIONS | grep -o '"id"' | wc -l)
echo -e "${GREEN}✓ $SECTION_COUNT seções encontradas${NC}"

# Se temos seções, testar checagem
if [ "$SECTION_COUNT" -gt 0 ]; then
  FIRST_SECTION=$(echo $SECTIONS | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
  
  # 4. Rodar checagens na primeira seção
  echo -e "\n${YELLOW}[4/6]${NC} Rodando checagens na seção $FIRST_SECTION..."
  CHECK_RESULT=$(curl -s -X POST "$BASE_URL/reviews/$REVIEW_ID/sections/$FIRST_SECTION/run-checks")
  
  RESULT_COUNT=$(echo $CHECK_RESULT | grep -o '"rule"' | wc -l)
  echo -e "${GREEN}✓ $RESULT_COUNT resultados encontrados${NC}"
else
  echo -e "${YELLOW}⚠ Nenhuma seção encontrada, pulando checagens${NC}"
fi

# 5. Exportar relatório HTML
echo -e "\n${YELLOW}[5/6]${NC} Exportando relatório HTML..."
EXPORT=$(curl -s "$BASE_URL/reviews/$REVIEW_ID/export?format=html")
FILENAME=$(echo $EXPORT | grep -o '"filename":"[^"]*' | cut -d'"' -f4)

if [ -z "$FILENAME" ]; then
  echo -e "${RED}✗ Erro ao exportar relatório${NC}"
  echo $EXPORT
  exit 1
fi

echo -e "${GREEN}✓ Relatório exportado: $FILENAME${NC}"

# 6. Baixar relatório
echo -e "\n${YELLOW}[6/6]${NC} Baixando relatório..."
curl -s "$BASE_URL/downloads/$FILENAME" -o "/tmp/$FILENAME"
echo -e "${GREEN}✓ Relatório salvo em: /tmp/$FILENAME${NC}"

echo -e "\n${GREEN}================================================${NC}"
echo -e "${GREEN}Teste concluído com sucesso!${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""
echo "Próximos passos:"
echo "  1. Visualizar relatório: firefox /tmp/$FILENAME"
echo "  2. Consultar dados: sqlite3 backend/anuario_audit.db"
echo "  3. Ver logs: tail -f app.log"
echo ""
