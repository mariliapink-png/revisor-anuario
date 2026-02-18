import requests
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO
import logging
from typing import List, Tuple, Optional, Dict, Any
from app.config import REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)


class SectionExtractor:
    """Extrai conteúdo e tabelas de uma seção específica."""
    
    def __init__(self, url: str, anchor: Optional[str] = None):
        self.url = url
        self.anchor = anchor
    
    def fetch_page(self) -> BeautifulSoup:
        """Baixa a página HTML."""
        try:
            headers = {"User-Agent": USER_AGENT}
            response = requests.get(self.url, timeout=REQUEST_TIMEOUT, headers=headers)
            response.raise_for_status()
            return BeautifulSoup(response.content, "lxml")
        except Exception as e:
            logger.error(f"Erro ao baixar {self.url}: {e}")
            raise
    
    def extract_section_block(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Se anchor existe, extrai bloco do elemento id=anchor até o próximo header.
        Senão, retorna o soup inteiro.
        """
        if not self.anchor:
            return soup.body or soup
        
        # Procura elemento com id=anchor
        target = soup.find(id=self.anchor)
        if not target:
            logger.warning(f"Anchor #{self.anchor} não encontrado em {self.url}")
            return soup.body or soup
        
        # Coleta elementos até próximo header (h1, h2, h3)
        block_elements = [target]
        current = target.next_sibling
        
        while current:
            if isinstance(current, str):
                if current.strip():
                    block_elements.append(current)
            else:
                # Parar se encontrar header
                if current.name and current.name in ["h1", "h2", "h3"]:
                    break
                block_elements.append(current)
            current = current.next_sibling
        
        # Criar novo objeto com os elementos
        wrapper = BeautifulSoup("<div></div>", "html.parser")
        for elem in block_elements:
            if isinstance(elem, str):
                wrapper.div.append(elem)
            else:
                wrapper.div.append(elem)
        
        return wrapper
    
    def extract_text(self, soup: BeautifulSoup) -> str:
        """Extrai todo o texto da seção."""
        return soup.get_text(separator=" ", strip=True)
    
    def extract_tables(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """
        Extrai todas as tabelas <table> da seção.
        Para cada tabela: {dataframe, caption, table_html, notes_text, source}
        """
        tables = []
        
        for table in soup.find_all("table"):
            table_data = {}
            
            # Caption
            caption_elem = table.find("caption")
            caption = caption_elem.get_text(strip=True) if caption_elem else ""
            table_data["caption"] = caption
            
            # HTML da tabela
            table_data["table_html"] = str(table)
            
            # Converter para DataFrame
            try:
                df = pd.read_html(
                    StringIO(str(table)),
                    decimal=",",
                    thousands=".",
                    flavor="lxml"
                )[0]
                table_data["dataframe"] = df
            except Exception as e:
                logger.warning(f"Erro ao parsear tabela: {e}")
                table_data["dataframe"] = None
            
            # Notas: extrair até 10 siblings abaixo da tabela
            notes_parts = []
            current = table.next_sibling
            sibling_count = 0
            
            while current and sibling_count < 10:
                if isinstance(current, str):
                    text = current.strip()
                    if text:
                        notes_parts.append(text)
                else:
                    if current.name in ["h1", "h2", "h3", "table"]:
                        break
                    notes_parts.append(current.get_text(strip=True))
                    sibling_count += 1
                
                current = current.next_sibling
            
            notes_text = " ".join(notes_parts)
            table_data["notes_text"] = notes_text
            
            # Detectar fonte
            source = ""
            for text in [caption, notes_text]:
                if "Fonte:" in text:
                    # Extrair texto após "Fonte:"
                    parts = text.split("Fonte:")
                    if len(parts) > 1:
                        source = parts[1].split("\n")[0].strip()
                        break
            
            table_data["source"] = source
            tables.append(table_data)
        
        logger.info(f"Extraídas {len(tables)} tabelas da seção")
        return tables
    
    def extract_all(self) -> Dict[str, Any]:
        """Extrai seção completa: texto + tabelas."""
        soup = self.fetch_page()
        section_block = self.extract_section_block(soup)
        
        return {
            "url": self.url,
            "anchor": self.anchor,
            "text": self.extract_text(section_block),
            "tables": self.extract_tables(section_block),
        }
