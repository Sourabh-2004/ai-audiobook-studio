import asyncio
import logging
import random
from typing import List, Dict, Union, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
import trafilatura

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/116.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0",
]

class WebScraper:
    """
    A comprehensive async web scraping module for the AI Novel Condenser project.
    Utilizes httpx, BeautifulSoup4, and trafilatura for robust extraction.
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.client_options = {
            "timeout": self.timeout,
            "follow_redirects": True
        }

    def _get_random_headers(self) -> Dict[str, str]:
        """Returns headers with a random User-Agent for polite scraping."""
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def _fetch(self, url: str) -> Optional[str]:
        """Fetch the URL content with async httpx, handling errors and rate limits."""
        async with httpx.AsyncClient(**self.client_options) as client:
            try:
                response = await client.get(url, headers=self._get_random_headers())
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 403:
                    logger.error(f"403 Forbidden: Access denied for {url}")
                elif status == 404:
                    logger.error(f"404 Not Found: {url}")
                elif status == 429:
                    logger.error(f"429 Too Many Requests: Rate limited for {url}")
                else:
                    logger.error(f"HTTP Error {status} for {url}: {e}")
            except httpx.RequestError as e:
                logger.error(f"Request error for {url}: {e}")
        return None

    async def scrape_single_page(self, url: str) -> dict:
        """
        Extract chapter content from any URL.
        Returns {"title": str, "content": str, "word_count": int, "source_url": str}
        """
        html_content = await self._fetch(url)
        if not html_content:
            return {"title": "Error", "content": "", "word_count": 0, "source_url": url}

        # Try to extract with trafilatura first (best for articles/chapters)
        extracted = trafilatura.extract(
            html_content,
            include_comments=False,
            include_tables=False,
            no_fallback=False
        )

        # Fallback to bs4 if trafilatura fails
        if not extracted:
            soup = BeautifulSoup(html_content, "lxml")
            # Attempt to extract text from common content tags
            paragraphs = soup.find_all(['p', 'div'])
            extracted = "\n".join([p.get_text(separator="\n", strip=True) for p in paragraphs if p.get_text(strip=True)])

        content = extracted if extracted else ""
        word_count = len(content.split())
        
        # Get title
        soup = BeautifulSoup(html_content, "lxml")
        title_tag = soup.find('title')
        title = title_tag.text.strip() if title_tag else "Unknown Title"

        return {
            "title": title,
            "content": content,
            "word_count": word_count,
            "source_url": url
        }

    async def scrape_chapter_list(self, url: str) -> List[dict]:
        """
        Detect and list all chapter links on a table-of-contents page.
        Returns list of {"title": str, "url": str}
        """
        html_content = await self._fetch(url)
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, "lxml")
        chapters = []
        # Basic heuristic: look for links containing 'chapter' in text or href
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            text = a_tag.get_text(strip=True)
            if not text:
                continue
                
            href_lower = href.lower()
            text_lower = text.lower()
            
            if 'chapter' in text_lower or 'chapter' in href_lower:
                full_url = urljoin(url, href)
                # Avoid duplicates
                if not any(c['url'] == full_url for c in chapters):
                    chapters.append({"title": text, "url": full_url})
        
        return chapters

    async def scrape_multiple_chapters(self, urls: List[str], delay: float = 1.5) -> List[dict]:
        """
        Batch scrape multiple chapter URLs with rate limiting.
        """
        results = []
        for i, url in enumerate(urls):
            logger.info(f"Scraping ({i+1}/{len(urls)}): {url}")
            data = await self.scrape_single_page(url)
            results.append(data)
            
            # Apply polite delay between requests, except for the last one
            if i < len(urls) - 1:
                sleep_time = delay + random.uniform(0.1, 0.5)
                await asyncio.sleep(sleep_time)
                
        return results

    async def scrape_royal_road(self, url: str) -> Union[Dict, List[Dict]]:
        """
        Specialized scraper for Royal Road fiction (royalroad.com).
        If URL is a chapter, parses chapter content directly.
        If URL is a fiction page, returns list of chapters.
        """
        html_content = await self._fetch(url)
        if not html_content:
            return {"title": "Error", "content": "", "word_count": 0, "source_url": url}

        soup = BeautifulSoup(html_content, "lxml")
        
        # Check if it's a fiction page (Table of Contents)
        if "/fiction/" in url and "/chapter/" not in url:
            chapters = []
            tbody = soup.find('tbody')
            if tbody:
                for row in tbody.find_all('tr'):
                    a_tag = row.find('a', href=True)
                    if a_tag:
                        full_url = urljoin("https://www.royalroad.com", a_tag['href'])
                        title = a_tag.get_text(strip=True)
                        chapters.append({"title": title, "url": full_url})
            return chapters
            
        # It's a chapter page
        title_tag = soup.find('h1', class_='font-white')
        title = title_tag.get_text(strip=True) if title_tag else "Unknown Royal Road Chapter"
        
        # Parse chapter content div directly
        content_div = soup.find('div', class_='chapter-content')
        if content_div:
            # Preserve paragraphs
            paragraphs = content_div.find_all('p')
            content = "\n\n".join(p.get_text(strip=True) for p in paragraphs)
        else:
            content = ""
            
        word_count = len(content.split())
        return {
            "title": title,
            "content": content,
            "word_count": word_count,
            "source_url": url
        }

    async def close(self):
        """Cleanup resources. No persistent client to close in current implementation."""
        pass

    async def preview_url(self, url: str) -> dict:
        """
        Quick preview showing title, word count, first 500 chars.
        """
        data = await self.scrape_single_page(url)
        preview_content = data["content"][:500] + ("..." if len(data["content"]) > 500 else "")
        return {
            "title": data["title"],
            "word_count": data["word_count"],
            "preview": preview_content,
            "source_url": data["source_url"]
        }

def scrape_sync(url: str) -> dict:
    """
    Synchronous wrapper for scrape_single_page for simple usage.
    """
    scraper = WebScraper()
    return asyncio.run(scraper.scrape_single_page(url))
