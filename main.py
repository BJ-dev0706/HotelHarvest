import requests
from bs4 import BeautifulSoup
import os
import time
from urllib.parse import urljoin, urlparse
import logging
from typing import Set, Dict, List, Tuple, Optional
from markdownify import markdownify
import json
import sys
import re
import tldextract

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("website_scraper.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("WebsiteScraper")

class WebsiteScraper:
    def __init__(self, base_url: str, delay: float = 1.0, max_booking_urls: int = 5):
        self.base_url = base_url
        self.parsed_base_url = urlparse(base_url)
        self.domain = self.parsed_base_url.netloc
        
        # Extract the root domain (e.g., chouchouhotel.com from www.chouchouhotel.com or en.chouchouhotel.com)
        extracted = tldextract.extract(base_url)
        self.root_domain = f"{extracted.domain}.{extracted.suffix}"
        logger.info(f"Root domain identified as: {self.root_domain}")
        
        self.delay = delay
        self.visited_urls: Set[str] = set()
        self.markdown_content: Dict[str, str] = {}
        
        # Define priority paths to look for, including language-specific paths
        self.priority_paths = [
            '/rooms', '/suites', '/accommodations', '/lodging',
            '/facilities', '/amenities', '/services', 
            '/photos', '/gallery', '/images',
            '/spa', '/restaurant', '/dining',
            # Language-specific paths
            '/en/rooms', '/en/suites', '/en/accommodations', '/en/facilities',
            '/en/amenities', '/en/photos', '/en/gallery', '/en/dining',
            # Other common language patterns
            '/fr/chambres', '/de/zimmer', '/es/habitaciones', '/it/camere',
            # Common URL patterns with hyphens
            '/room-types', '/our-rooms', '/guest-rooms', '/our-suites',
            '/hotel-facilities', '/hotel-amenities', '/photo-gallery',
            # Booking related paths
            '/booking', '/reserve', '/reservation', '/book-now', '/book',
            '/en/booking', '/en/reserve', '/en/book-now'
        ]
        
        # Common language prefixes to check for language-specific subdomains
        self.language_prefixes = ['en', 'fr', 'de', 'es', 'it', 'nl', 'pt', 'ru', 'zh', 'ja', 'ko']
        
        # Track main page links to prioritize direct subpages
        self.main_page_links: Set[str] = set()
        
        # Track potential external booking domains
        self.potential_booking_domains = [
            'booking', 'reserve', 'reservation', 'book'
        ]
        
        # Limit the number of booking URLs to crawl
        self.max_booking_urls = max_booking_urls
        self.booking_urls_crawled = 0
        self.booking_domains_seen: Set[str] = set()
        
        # Track language-specific subdomains we've found
        self.language_subdomains: Set[str] = set()
        
    def validate_url(self) -> Tuple[bool, Optional[str]]:
        """
        Validate if the base URL exists and handle redirects.
        
        Returns:
            Tuple of (is_valid, redirected_url)
            - is_valid: True if the URL exists (possibly after redirection)
            - redirected_url: The URL after redirection, or None if no redirection
        """
        try:
            # Set a timeout to avoid hanging on non-responsive sites
            response = requests.head(
                self.base_url, 
                headers={'User-Agent': 'Mozilla/5.0'}, 
                timeout=10,
                allow_redirects=True
            )
            
            # Check if the URL exists (2xx status code)
            if 200 <= response.status_code < 300:
                # Check if there was a redirect
                if response.url != self.base_url:
                    logger.info(f"URL was redirected: {self.base_url} -> {response.url}")
                    return True, response.url
                return True, None
            
            # Handle common error status codes
            if response.status_code == 404:
                logger.error(f"URL not found (404): {self.base_url}")
            elif response.status_code == 403:
                logger.error(f"Access forbidden (403): {self.base_url}")
            elif response.status_code == 500:
                logger.error(f"Server error (500): {self.base_url}")
            else:
                logger.error(f"URL validation failed with status code {response.status_code}: {self.base_url}")
            
            return False, None
            
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error: Could not connect to {self.base_url}")
            return False, None
        except requests.exceptions.Timeout:
            logger.error(f"Timeout error: {self.base_url} took too long to respond")
            return False, None
        except requests.exceptions.TooManyRedirects:
            logger.error(f"Too many redirects: {self.base_url}")
            return False, None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error validating URL {self.base_url}: {e}")
            return False, None
    
    def is_same_site(self, url: str) -> bool:
        """Check if URL belongs to the same website (including subdomains)"""
        parsed = urlparse(url)
        if not parsed.netloc:
            return False
            
        # Extract domain parts
        extracted = tldextract.extract(url)
        url_root_domain = f"{extracted.domain}.{extracted.suffix}"
        
        # Check if it's the same root domain
        return url_root_domain == self.root_domain
        
    def is_valid_url(self, url: str) -> bool:
        """Check if URL belongs to the same website (including subdomains) and is not a file/anchor"""
        parsed = urlparse(url)
        
        # Skip anchors, files, external links, mailto, tel, etc.
        if not parsed.netloc:
            return False
            
        # Check if it's the same site (including subdomains)
        if not self.is_same_site(url):
            # Allow external booking domains that might contain room information
            if any(booking_term in parsed.netloc.lower() for booking_term in self.potential_booking_domains) and \
               self.root_domain in parsed.netloc.lower():
                logger.info(f"Found potential external booking URL: {url}")
                return True
            return False
        
        # Skip common file extensions
        file_extensions = ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', 
                          '.css', '.js', '.ico', '.xml', '.zip', '.doc', '.docx']
        if any(parsed.path.lower().endswith(ext) for ext in file_extensions):
            return False
            
        return True
    
    def normalize_url(self, url: str) -> str:
        """Normalize URL by removing fragments and trailing slashes"""
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            # For booking URLs, strip out common parameters that just change the UI language or currency
            if self.is_booking_url(url):
                query_params = {}
                for param in parsed.query.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        # Skip language, currency, and session ID parameters
                        if key.lower() not in ['lang', 'language', 'currency', 'nsid', 'sessionid']:
                            query_params[key] = value
                if query_params:
                    normalized += '?' + '&'.join(f"{k}={v}" for k, v in query_params.items())
            else:
                normalized += f"?{parsed.query}"
        return normalized.rstrip('/')
    
    def is_priority_url(self, url: str) -> bool:
        """Check if URL contains priority paths we want to prioritize"""
        parsed = urlparse(url)
        path = parsed.path.lower()
        
        # Check if it's a language subdomain (e.g., en.chouchouhotel.com)
        extracted = tldextract.extract(url)
        if extracted.subdomain in self.language_prefixes:
            # Add this language subdomain to our tracked list
            self.language_subdomains.add(parsed.netloc)
            return True
            
        # Check if it's a booking URL (either internal or external)
        if any(booking_term in parsed.netloc.lower() for booking_term in self.potential_booking_domains):
            return True
            
        # Check if any priority path is in the URL path
        return any(priority in path for priority in self.priority_paths) or url in self.main_page_links
    
    def extract_booking_links(self, soup, url: str) -> List[str]:
        """Extract potential booking links from the page"""
        booking_links = []
        
        # Look for links with booking-related text or classes
        booking_indicators = ['book', 'reserve', 'booking', 'reservation', 'check availability']
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            text = a_tag.get_text().lower().strip()
            
            # Check if the link text contains booking indicators
            if any(indicator in text for indicator in booking_indicators) or \
               any(indicator in href.lower() for indicator in booking_indicators) or \
               a_tag.get('class') and any(cls and 'book' in cls.lower() for cls in a_tag.get('class')):
                
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)
                
                # Skip if it's a fragment or javascript
                if not parsed.netloc and (not parsed.path or parsed.path == '#' or 'javascript:' in href):
                    continue
                    
                booking_links.append(full_url)
                logger.info(f"Found booking link: {full_url}")
        
        return booking_links
    
    def is_booking_url(self, url: str) -> bool:
        """Check if URL is likely a booking page"""
        parsed = urlparse(url)
        
        # Check domain
        if any(term in parsed.netloc.lower() for term in self.potential_booking_domains):
            return True
            
        # Check path
        if any(term in parsed.path.lower() for term in ['book', 'reserve', 'reservation']):
            return True
            
        # Check query parameters
        if parsed.query and any(term in parsed.query.lower() for term in ['book', 'reserve', 'reservation']):
            return True
            
        return False
    
    def extract_language_variants(self, soup, url: str) -> List[str]:
        """Extract language-specific variants of the current page"""
        language_links = []
        
        # Look for language switcher links
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            
            # Skip empty or javascript links
            if not href or href.startswith('#') or href.startswith('javascript:'):
                continue
                
            # Check if it's a language link by looking at common patterns
            is_language_link = False
            
            # Check for language codes in the URL
            if re.search(r'/[a-z]{2}(/|$)', href) or re.search(r'lang=[a-z]{2}', href):
                is_language_link = True
                
            # Check for language names in the link text
            text = a_tag.get_text().lower().strip()
            language_names = ['english', 'français', 'deutsch', 'español', 'italiano', 'en', 'fr', 'de', 'es', 'it']
            if any(lang in text for lang in language_names):
                is_language_link = True
                
            # Check for language flags in the images
            img_tag = a_tag.find('img')
            if img_tag and img_tag.get('src') and ('flag' in img_tag.get('src').lower() or 'lang' in img_tag.get('src').lower()):
                is_language_link = True
                
            # Check for common language switcher classes
            classes = a_tag.get('class', [])
            if classes and any(cls and ('lang' in cls.lower() or 'language' in cls.lower()) for cls in classes):
                is_language_link = True
                
            if is_language_link:
                full_url = urljoin(url, href)
                
                # Only include if it's on the same site
                if self.is_same_site(full_url):
                    language_links.append(full_url)
                    logger.info(f"Found language variant: {full_url}")
        
        return language_links
    
    def extract_header_menu_links(self, soup, url: str) -> List[str]:
        """Extract links from the header/navigation menu"""
        menu_links = []
        
        # Look for navigation elements
        nav_elements = soup.find_all(['nav', 'header', 'div'], class_=lambda c: c and any(term in c.lower() for term in ['nav', 'menu', 'header', 'topbar']))
        
        if not nav_elements:
            # If no nav elements with those classes, try to find by role
            nav_elements = soup.find_all(attrs={"role": "navigation"})
            
        if not nav_elements:
            # If still no nav elements, look for common header IDs
            nav_elements = soup.find_all(id=lambda i: i and any(term in i.lower() for term in ['nav', 'menu', 'header', 'topbar']))
        
        # Process all navigation elements found
        for nav in nav_elements:
            for a_tag in nav.find_all('a', href=True):
                href = a_tag['href']
                
                # Skip empty or javascript links
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue
                    
                full_url = urljoin(url, href)
                
                # Only include if it's on the same site
                if self.is_same_site(full_url):
                    menu_links.append(full_url)
                    logger.info(f"Found menu link: {full_url}")
        
        return menu_links
    
    def crawl(self) -> Dict[str, str]:
        """Crawl the website and extract content"""
        try:
            # Start with the base URL
            urls_to_visit = [self.base_url]
            priority_urls = []
            
            # First, crawl the main page to extract important links
            response = requests.get(self.base_url, headers={'User-Agent': 'Mozilla/5.0'})
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract all links from the main page
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href']
                    if href and not href.startswith('#') and not href.startswith('javascript:'):
                        full_url = urljoin(self.base_url, href)
                        if self.is_valid_url(full_url):
                            normalized_url = self.normalize_url(full_url)
                            self.main_page_links.add(normalized_url)
                
                # Extract language variants
                language_links = self.extract_language_variants(soup, self.base_url)
                for link in language_links:
                    if self.is_valid_url(link):
                        normalized_url = self.normalize_url(link)
                        if normalized_url not in self.visited_urls:
                            priority_urls.append(normalized_url)
                            logger.info(f"Added language variant to priority queue: {normalized_url}")
                
                # Extract header/menu links
                menu_links = self.extract_header_menu_links(soup, self.base_url)
                for link in menu_links:
                    if self.is_valid_url(link):
                        normalized_url = self.normalize_url(link)
                        if normalized_url not in self.visited_urls:
                            priority_urls.append(normalized_url)
                            logger.info(f"Added menu link to priority queue: {normalized_url}")
                
                # Extract booking links
                booking_links = self.extract_booking_links(soup, self.base_url)
                for link in booking_links:
                    if self.is_valid_url(link):
                        normalized_url = self.normalize_url(link)
                        if normalized_url not in self.visited_urls:
                            priority_urls.append(normalized_url)
                            logger.info(f"Added booking link to priority queue: {normalized_url}")
            
            # Process the main page first
            self._process_url(self.base_url)
            
            # Then process all priority URLs (language variants, menu links, etc.)
            for url in priority_urls:
                if url not in self.visited_urls:
                    self._process_url(url)
                    
                    # After processing each priority URL, check for language subdomains
                    parsed = urlparse(url)
                    extracted = tldextract.extract(url)
                    
                    # If this is a language subdomain, explore it further
                    if extracted.subdomain in self.language_prefixes:
                        logger.info(f"Exploring language subdomain: {parsed.netloc}")
                        
                        # Get all links from this language subdomain
                        try:
                            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                            if response.status_code == 200:
                                soup = BeautifulSoup(response.text, 'html.parser')
                                
                                # Extract menu links from this language subdomain
                                subdomain_menu_links = self.extract_header_menu_links(soup, url)
                                for link in subdomain_menu_links:
                                    if self.is_valid_url(link) and link not in self.visited_urls:
                                        normalized_url = self.normalize_url(link)
                                        urls_to_visit.append(normalized_url)
                                        logger.info(f"Added language subdomain menu link: {normalized_url}")
                        except Exception as e:
                            logger.error(f"Error exploring language subdomain {url}: {e}")
            
            # Now process the remaining URLs
            while urls_to_visit:
                # Sort URLs to prioritize important ones
                urls_to_visit.sort(key=lambda u: 0 if self.is_priority_url(u) else 1)
                
                # Get the next URL to process
                url = urls_to_visit.pop(0)
                
                # Skip if already visited
                if url in self.visited_urls:
                    continue
                
                # Process the URL
                new_urls = self._process_url(url)
                
                # Add new URLs to the queue
                for new_url in new_urls:
                    if new_url not in self.visited_urls and new_url not in urls_to_visit:
                        urls_to_visit.append(new_url)
            
            logger.info(f"Crawling complete. Visited {len(self.visited_urls)} URLs.")
            return self.markdown_content
            
        except Exception as e:
            logger.error(f"Error crawling website: {e}")
            return self.markdown_content
    
    def _process_url(self, url: str) -> List[str]:
        """Process a single URL and return new URLs to visit"""
        # Skip if already visited
        normalized_url = self.normalize_url(url)
        if normalized_url in self.visited_urls:
            return []
            
        # Mark as visited
        self.visited_urls.add(normalized_url)
        
        # Check if it's a booking URL and we've reached the limit
        if self.is_booking_url(url):
            parsed = urlparse(url)
            if parsed.netloc in self.booking_domains_seen:
                # We've already seen this booking domain
                if self.booking_urls_crawled >= self.max_booking_urls:
                    logger.info(f"Skipping booking URL (reached limit): {url}")
                    return []
            else:
                # New booking domain
                self.booking_domains_seen.add(parsed.netloc)
                
            self.booking_urls_crawled += 1
        
        # Delay to be nice to the server
        time.sleep(self.delay)
        
        try:
            # Fetch the page
            logger.info(f"Fetching {url}")
            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            
            # Skip if not successful
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {url}: {response.status_code}")
                return []
                
            # Parse the page
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Convert to markdown
            content = self._html_to_markdown(soup)
            
            # Store the content
            self.markdown_content[url] = content
            
            # Extract new URLs
            new_urls = []
            
            # First, check for language variants
            language_links = self.extract_language_variants(soup, url)
            for link in language_links:
                if self.is_valid_url(link):
                    normalized_link = self.normalize_url(link)
                    if normalized_link not in self.visited_urls:
                        new_urls.append(normalized_link)
                        logger.info(f"Found language variant: {normalized_link}")
            
            # Then, check for header/menu links
            menu_links = self.extract_header_menu_links(soup, url)
            for link in menu_links:
                if self.is_valid_url(link):
                    normalized_link = self.normalize_url(link)
                    if normalized_link not in self.visited_urls:
                        new_urls.append(normalized_link)
                        logger.info(f"Found menu link: {normalized_link}")
            
            # Extract booking links
            if self.booking_urls_crawled < self.max_booking_urls:
                booking_links = self.extract_booking_links(soup, url)
                for link in booking_links:
                    if self.is_valid_url(link):
                        normalized_link = self.normalize_url(link)
                        if normalized_link not in self.visited_urls:
                            new_urls.append(normalized_link)
                            logger.info(f"Found booking link: {normalized_link}")
            
            # Extract all other links
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                
                # Skip empty or javascript links
                if not href or href.startswith('#') or href.startswith('javascript:'):
                    continue
                    
                # Get the full URL
                full_url = urljoin(url, href)
                
                # Check if it's valid
                if self.is_valid_url(full_url):
                    normalized_link = self.normalize_url(full_url)
                    if normalized_link not in self.visited_urls and normalized_link not in new_urls:
                        new_urls.append(normalized_link)
            
            return new_urls
            
        except Exception as e:
            logger.error(f"Error processing {url}: {e}")
            return []
    
    def _html_to_markdown(self, soup) -> str:
        """Convert HTML to markdown"""
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.extract()
            
        # Convert to markdown
        html = str(soup)
        markdown = markdownify(html, heading_style="ATX")
        
        return markdown


def get_website_markdown(url, delay=1.0, max_booking_urls=5):
    """Scrape a website and convert it to markdown."""
    try:
        # Create a scraper instance
        scraper = WebsiteScraper(url, delay, max_booking_urls)
        
        # Validate the URL first
        is_valid, redirected_url = scraper.validate_url()
        
        if not is_valid:
            logger.error(f"URL validation failed for {url}. Stopping processing.")
            return None
        
        # If the URL was redirected, update the scraper with the new URL
        if redirected_url:
            logger.info(f"Following redirect from {url} to {redirected_url}")
            # Ask the user if they want to follow the redirect
            print(f"The URL {url} redirects to {redirected_url}")
            choice = input("Do you want to follow this redirect? (y/n): ").strip().lower()
            
            if choice == 'y' or choice == 'yes':
                # Create a new scraper with the redirected URL
                scraper = WebsiteScraper(redirected_url, delay, max_booking_urls)
                url = redirected_url
            else:
                logger.info("User chose not to follow the redirect. Stopping processing.")
                return None
        
        # Crawl the website
        logger.info(f"Starting to crawl {url}")
        content = scraper.crawl()
        
        if not content:
            logger.warning(f"No content found for {url}")
            return None
        
        logger.info(f"Successfully scraped {len(content)} pages from {url}")
        return content
    
    except Exception as e:
        logger.error(f"Error scraping website {url}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def save_markdown_content(markdown_content, url):
    """Save the markdown content to a JSON file."""
    try:
        # Create the output directory if it doesn't exist
        output_dir = os.path.join("MCP", "data", "scraped")
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate a unique ID based on the domain name
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        
        # Create a more filename-friendly version of the domain
        safe_domain = re.sub(r'[\\/*?:"<>|]', '_', domain)
        
        # Generate a timestamp for uniqueness
        timestamp = int(time.time())
        
        # Create the output filename
        output_filename = f"{safe_domain}_{timestamp}.json"
        output_path = os.path.join(output_dir, output_filename)
        
        # Convert to JSON and save
        output_data = {
            "url": url,
            "timestamp": timestamp,
            "domain": domain,
            "content": markdown_content
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Content saved to {output_path}")
        return output_path
    
    except Exception as e:
        logger.error(f"Error saving content: {e}")
        return None


def structure_content(input_file_path):
    """Structure the content using structure_info.py."""
    try:
        # Import the structure_content function from structure_info.py
        from structure_info import structure_content as structure_content_func
        from structure_info import save_structured_content
        
        # Load the content from the file
        with open(input_file_path, 'r', encoding='utf-8') as f:
            content_data = json.load(f)
        
        # Structure the content
        structured_content = structure_content_func(input_file_path, content_data)
        
        # Save the structured content
        output_dir = os.path.join("MCP", "data", "structured")
        output_path = save_structured_content(structured_content, input_file_path, output_dir)
        
        logger.info(f"Structured content saved to {output_path}")
        return output_path
    except Exception as e:
        logger.error(f"Error structuring content: {e}")
        return None


def download_images(json_file_path, min_width=800, min_height=600, min_size=50, delay=1.0, max_pages=20):
    """Download images using the URL from the structured JSON file."""
    try:
        # Import the necessary function from images.py
        from images import extract_url_from_structured_json, ImageDownloader
        
        # Extract the URL and ID from the structured JSON
        url, unique_id = extract_url_from_structured_json(json_file_path)
        
        if not url:
            logger.error(f"No URL found in {json_file_path}")
            return False
        
        # Add http:// if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            logger.info(f"Added https:// to URL: {url}")
        
        logger.info(f"Starting image download from {url} with ID: {unique_id}")
        logger.info(f"Minimum dimensions: {min_width}x{min_height} pixels")
        logger.info(f"Minimum size: {min_size} KB")
        
        # Create the image downloader with the unique ID
        downloader = ImageDownloader(
            base_url=url,
            unique_id=unique_id,
            min_width=min_width,
            min_height=min_height,
            min_size_kb=min_size,
            delay=delay,
            max_pages=max_pages
        )
        
        # Download images
        start_time = time.time()
        image_count = downloader.crawl()
        elapsed_time = time.time() - start_time
        
        logger.info(f"Download complete! Downloaded {image_count} images in {elapsed_time:.1f} seconds")
        
        if image_count > 0:
            logger.info(f"Images saved to {downloader.image_dir}")
            return True
        else:
            logger.warning(f"No images were downloaded for {url}")
            return False
    except Exception as e:
        logger.error(f"Error downloading images: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def process_hotel(url, delay=1.0, max_booking_urls=5, min_width=800, min_height=600, min_size=50, max_pages=20):
    """Process a single hotel URL through the entire pipeline."""
    try:
        logger.info(f"Starting processing for hotel: {url}")
        
        # Step 1: Scrape website content
        logger.info(f"Step 1: Scraping website content for {url}")
        markdown_content = get_website_markdown(url, delay, max_booking_urls)
        
        if not markdown_content:
            logger.error(f"Failed to scrape content from {url}")
            return False
        
        # Step 2: Save markdown content
        logger.info(f"Step 2: Saving markdown content for {url}")
        output_path = save_markdown_content(markdown_content, url)
        
        # Step 3: Structure content
        logger.info(f"Step 3: Structuring content for {url}")
        structured_path = structure_content(output_path)
        
        if not structured_path:
            logger.error(f"Failed to structure content for {url}")
            return False
        
        # Step 4: Download images
        logger.info(f"Step 4: Downloading images for {url}")
        image_success = download_images(
            structured_path, 
            min_width=min_width, 
            min_height=min_height, 
            min_size=min_size, 
            delay=delay, 
            max_pages=max_pages
        )
        
        if not image_success:
            logger.warning(f"Image download may have had issues for {url}")
        
        logger.info(f"Completed processing for hotel: {url}")
        return True
    
    except Exception as e:
        logger.error(f"Error processing hotel {url}: {e}")
        return False


def process_hotel_list(urls, delay=1.0, max_booking_urls=5, min_width=800, min_height=600, min_size=50, max_pages=20):
    """Process a list of hotel URLs sequentially."""
    results = []
    
    for i, url in enumerate(urls):
        logger.info(f"Processing hotel {i+1}/{len(urls)}: {url}")
        
        success = process_hotel(
            url, 
            delay=delay, 
            max_booking_urls=max_booking_urls,
            min_width=min_width,
            min_height=min_height,
            min_size=min_size,
            max_pages=max_pages
        )
        
        results.append({
            "url": url,
            "success": success
        })
        
        logger.info(f"Completed hotel {i+1}/{len(urls)}")
        
        # Add a delay between hotels to be nice to servers
        if i < len(urls) - 1:
            logger.info(f"Waiting {delay*2} seconds before processing next hotel...")
            time.sleep(delay * 2)
    
    # Print summary
    logger.info("=== Processing Summary ===")
    successful = sum(1 for r in results if r["success"])
    logger.info(f"Successfully processed {successful}/{len(urls)} hotels")
    
    for i, result in enumerate(results):
        status = "✓ Success" if result["success"] else "✗ Failed"
        logger.info(f"{i+1}. {result['url']} - {status}")
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Scrape hotel websites, structure content, and download images")
    parser.add_argument("--url", "-u", help="URL of the website to scrape")
    parser.add_argument("--file", "-f", help="File containing list of URLs to scrape (one per line)")
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="Delay between requests in seconds")
    parser.add_argument("--max-booking", "-b", type=int, default=5, help="Maximum number of booking URLs to crawl")
    parser.add_argument("--min-width", "-w", type=int, default=800, help="Minimum image width in pixels")
    parser.add_argument("--min-height", "-H", type=int, default=600, help="Minimum image height in pixels")
    parser.add_argument("--min-size", "-s", type=int, default=50, help="Minimum image size in KB")
    parser.add_argument("--max-pages", "-p", type=int, default=20, help="Maximum number of pages to crawl for images")
    
    args = parser.parse_args()
    
    # Create the base data directory structure
    base_data_dir = "MCP/data"
    dirs_to_create = [
        base_data_dir,
        os.path.join(base_data_dir, "scraped"),
        os.path.join(base_data_dir, "structured"),
        os.path.join(base_data_dir, "images")
    ]
    
    for directory in dirs_to_create:
        os.makedirs(directory, exist_ok=True)
        logger.info(f"Ensured directory exists: {directory}")
    
    # Process a list of URLs from a file
    if args.file:
        try:
            with open(args.file, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
            
            if not urls:
                logger.error(f"No URLs found in {args.file}")
                sys.exit(1)
            
            logger.info(f"Found {len(urls)} URLs in {args.file}")
            process_hotel_list(
                urls, 
                delay=args.delay, 
                max_booking_urls=args.max_booking,
                min_width=args.min_width,
                min_height=args.min_height,
                min_size=args.min_size,
                max_pages=args.max_pages
            )
        
        except Exception as e:
            logger.error(f"Error processing URL list: {e}")
            sys.exit(1)
    
    # Process a single URL
    elif args.url:
        process_hotel(
            args.url, 
            delay=args.delay, 
            max_booking_urls=args.max_booking,
            min_width=args.min_width,
            min_height=args.min_height,
            min_size=args.min_size,
            max_pages=args.max_pages
        )
    
    else:
        # Prompt user to enter a URL or file
        print("Please choose an option:")
        print("1. Enter a single hotel URL")
        print("2. Enter a file containing multiple hotel URLs")
        choice = input("> ").strip()
        
        if choice == "1":
            print("Enter the URL of the hotel website:")
            url = input("> ").strip()
            if url:
                process_hotel(
                    url, 
                    delay=args.delay, 
                    max_booking_urls=args.max_booking,
                    min_width=args.min_width,
                    min_height=args.min_height,
                    min_size=args.min_size,
                    max_pages=args.max_pages
                )
            else:
                print("No URL provided. Exiting.")
        
        elif choice == "2":
            print("Enter the path to the file containing URLs (one per line):")
            file_path = input("> ").strip()
            if file_path and os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    urls = [line.strip() for line in f if line.strip()]
                
                if urls:
                    process_hotel_list(
                        urls, 
                        delay=args.delay, 
                        max_booking_urls=args.max_booking,
                        min_width=args.min_width,
                        min_height=args.min_height,
                        min_size=args.min_size,
                        max_pages=args.max_pages
                    )
                else:
                    print(f"No URLs found in {file_path}")
            else:
                print(f"File not found: {file_path}")
        
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
