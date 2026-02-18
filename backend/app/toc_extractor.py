import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
from typing import List, Tuple
from app.config import REQUEST_TIMEOUT, USER_AGENT
import logging

logger = logging.getLogger(__name__)


class TOCExtractor:
    """Extrai automaticamente o índice (TOC) de um site HTML."""
    
    def __init__(self, start_url: str):
        self.start_url = start_url
        self.domain = urlparse(start_url).netloc
        self.base_url = f"{urlparse(start_url).scheme}://{self.domain}"
    
    def fetch_page(self, url: str) -> BeautifulSoup:
        """Baixa uma página HTML."""
        try:
            headers = {"User-Agent": USER_AGENT}
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
            response.raise_for_status()
            return BeautifulSoup(response.content, "lxml")
        except Exception as e:
            logger.error(f"Erro ao baixar {url}: {e}")
            raise
    
    def _normalize_url(self, href: str) -> Tuple[str, str]:
        """
        Normaliza URL e extrai anchor.
        Retorna (url_sem_anchor, anchor) ou (url, "")
        """
        if not href:
            return "", ""
        
        # Ignorar âncoras que não levam a lugar
        if href.startswith("#"):
            return "", href
        
        # Converter URL relativa em absoluta
        absolute_url = urljoin(self.start_url, href)
        
        # Separar anchor
        parsed = urlparse(absolute_url)
        anchor = parsed.fragment
        
        # URL sem anchor
        url_without_anchor = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            ""
        ))
        
        return url_without_anchor, anchor
    
    def _find_toc_container(self, soup: BeautifulSoup) -> BeautifulSoup:
        """
        Localiza o container do TOC usando heurística:
        - procura por nav, aside, div com classe 'toc'|'menu'|'sidebar'
        - retorna elemento com maior número de <a> internos (mesmo domínio)
        """
        candidates = []
        
        # Candidatos óbvios
        for tag_name in ["nav", "aside"]:
            for tag in soup.find_all(tag_name):
                links = self._count_internal_links(tag)
                if links > 0:
                    candidates.append((tag, links))
        
        # Divs com classes sugestivas
        for tag in soup.find_all("div", class_=lambda x: x and any(k in x.lower() for k in ["toc", "menu", "sidebar", "nav", "index"])):
            links = self._count_internal_links(tag)
            if links > 0:
                candidates.append((tag, links))
        
        # Se nenhum encontrado, pegar div com mais links internos
        if not candidates:
            for tag in soup.find_all("div"):
                links = self._count_internal_links(tag)
                if links >= 5:  # threshold mínimo
                    candidates.append((tag, links))
        
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]
        
        # Fallback: retornar soup inteira
        return soup
    
    def _count_internal_links(self, tag) -> int:
        """Conta quantos <a> internos existem em tag."""
        count = 0
        for link in tag.find_all("a", href=True):
            url, _ = self._normalize_url(link.get("href"))
            if url and self.domain in urlparse(url).netloc:
                count += 1
        return count
    
    def _infer_level(self, element, all_elements: List) -> int:
        """Infere o nível de aninhamento do elemento na árvore."""
        # Heurística simples: contar <ul> ancestrais
        level = 1
        parent = element.parent
        while parent and parent.name in ["ul", "ol", "li"]:
            if parent.name in ["ul", "ol"]:
                level += 1
            parent = parent.parent
        return level
    
    def extract_toc(self) -> List[dict]:
        """
        Extrai o TOC e retorna lista de seções.
        Cada seção: {title, url, anchor, level}
        """
        soup = self.fetch_page(self.start_url)
        toc_container = self._find_toc_container(soup)
        
        sections = []
        seen_urls = set()
        
        # Extrair todos os <a> do container
        links = toc_container.find_all("a", href=True)
        
        for i, link in enumerate(links):
            href = link.get("href", "")
            title = link.get_text(strip=True)
            
            if not title or not href:
                continue
            
            url, anchor = self._normalize_url(href)
            
            # Ignorar links vazios ou repetidos
            if not url or url in seen_urls:
                continue
            
            # Ignorar links para fora do domínio
            if self.domain not in urlparse(url).netloc:
                continue
            
            seen_urls.add(url)
            
            level = self._infer_level(link, links)
            
            sections.append({
                "title": title,
                "url": url,
                "anchor": anchor,
                "level": level,
            })
        
        logger.info(f"Extraído {len(sections)} seções do TOC")
        return sections
