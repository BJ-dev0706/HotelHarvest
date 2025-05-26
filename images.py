import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import logging
import time
from PIL import Image
from io import BytesIO
import hashlib
import argparse
from typing import Set, List, Tuple
import json
import glob
import subprocess
import sys
import tempfile
import concurrent.futures

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ImageDownloader:
    def __init__(self, base_url, unique_id=None, min_width=800, min_height=600, min_size_kb=50, delay=1.0, max_pages=20):
        self.base_url = base_url
        self.min_width = min_width
        self.min_height = min_height
        self.min_size_kb = min_size_kb
        self.delay = delay
        self.max_pages = max_pages
        
        # Extract domain for folder name
        parsed_url = urlparse(base_url)
        self.domain = parsed_url.netloc
        
        # Use the provided unique_id if available, otherwise generate from URL
        if unique_id:
            folder_name = unique_id
            logger.info(f"Using provided ID for folder name: {folder_name}")
        else:
            folder_name = self._get_folder_name_from_url(base_url)
            logger.info(f"Generated folder name from URL: {folder_name}")
        
        # Set up standardized directory structure with MCP/data as base
        self.base_dir = "MCP/data"
        self.images_base_dir = os.path.join(self.base_dir, "images")
        
        # Create base directories if they don't exist
        os.makedirs(self.images_base_dir, exist_ok=True)
        
        self.image_dir = os.path.join(self.images_base_dir, folder_name)
        if not os.path.exists(self.image_dir):
            os.makedirs(self.image_dir, exist_ok=True)
        
        # Track visited URLs and downloaded images
        self.visited_urls = set()
        self.image_urls = set()
        self.downloaded_count = 0
        
        # Priority pages to crawl first
        self.priority_pages = []
        
        # Headers to mimic a browser
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    
    def _get_folder_name_from_url(self, url):
        """Generate a safe folder name from a URL."""
        parsed_url = urlparse(url)
        
        # Start with the domain
        folder_name = parsed_url.netloc.replace(".", "_")
        
        # Add the path if it exists (but clean it up)
        if parsed_url.path and parsed_url.path != "/":
            # Remove leading and trailing slashes
            path = parsed_url.path.strip("/")
            # Replace special characters
            path = re.sub(r'[\\/*?:"<>|]', "_", path)
            # Limit length
            if len(path) > 50:
                path = path[:50]
            # Add to folder name
            folder_name += "_" + path
        
        # Ensure the folder name is not too long
        if len(folder_name) > 100:
            folder_name = folder_name[:100]
        
        return folder_name
    
    def is_valid_url(self, url: str) -> bool:
        """Check if URL might contain relevant images for the hotel."""
        try:
            parsed_url = urlparse(url)
            if not parsed_url.netloc:
                return False
            
            # Accept the main domain
            if parsed_url.netloc == self.domain:
                return True
            
            # Also accept common subdomains and CDN patterns
            root_domain = '.'.join(self.domain.split('.')[-2:])  # e.g., extract "example.com" from "www.example.com"
            if root_domain in parsed_url.netloc:
                return True
            
            # Accept common CDN domains if they appear to be serving images
            cdn_patterns = ['cloudfront.net', 'akamaized.net', 'cloudinary.com', 'imgix.net', 'amazonaws.com']
            if any(cdn in parsed_url.netloc for cdn in cdn_patterns):
                path_ext = os.path.splitext(parsed_url.path)[1].lower()
                image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
                return path_ext in image_extensions
            
            return False
        except:
            return False
    
    def get_image_info(self, img_url: str) -> Tuple[bool, str, int, int, int]:
        """
        Get image information and check if it meets the minimum requirements.
        
        Returns:
            Tuple of (is_valid, content_type, width, height, size_bytes)
        """
        try:
            # Send a HEAD request first to check content type and size
            head_response = requests.head(img_url, headers=self.headers, timeout=10)
            
            # Check if it's an image
            content_type = head_response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                return False, content_type, 0, 0, 0
            
            # Check file size if available
            content_length = int(head_response.headers.get('Content-Length', 0))
            if content_length > 0 and content_length < self.min_size_kb * 1024:
                return False, content_type, 0, 0, content_length
            
            # If we can't determine size from headers or it's large enough, download the image
            response = requests.get(img_url, headers=self.headers, timeout=10)
            img_data = BytesIO(response.content)
            
            # Check actual file size
            size_bytes = len(response.content)
            if size_bytes < self.min_size_kb * 1024:
                return False, content_type, 0, 0, size_bytes
            
            # Check dimensions
            img = Image.open(img_data)
            width, height = img.size
            
            if width < self.min_width or height < self.min_height:
                return False, content_type, width, height, size_bytes
            
            return True, content_type, width, height, size_bytes
            
        except Exception as e:
            logger.debug(f"Error checking image {img_url}: {e}")
            return False, "", 0, 0, 0
    
    def get_image_hash(self, img_data: bytes) -> str:
        """Generate a hash for the image data to detect duplicates."""
        return hashlib.md5(img_data).hexdigest()
    
    def download_image(self, img_url: str, max_retries=3) -> bool:
        """Download an image with retry mechanism."""
        for attempt in range(max_retries):
            try:
                # Get full URL
                img_url = urljoin(self.base_url, img_url)
                
                # Check image info
                is_valid, content_type, width, height, size_bytes = self.get_image_info(img_url)
                
                if not is_valid:
                    logger.debug(f"Skipping image {img_url} (w:{width}, h:{height}, size:{size_bytes/1024:.1f}KB)")
                    return False
                
                # Download the image
                response = requests.get(img_url, headers=self.headers, timeout=10)
                img_data = response.content
                
                # Check for duplicates using hash
                img_hash = self.get_image_hash(img_data)
                if img_hash in self.image_urls:
                    logger.debug(f"Skipping duplicate image {img_url}")
                    return False
                
                self.image_urls.add(img_hash)
                
                # Generate filename from URL
                img_filename = os.path.basename(urlparse(img_url).path)
                if not img_filename or "." not in img_filename:
                    # Use hash as filename if URL doesn't have a valid filename
                    extension = content_type.split('/')[-1]
                    img_filename = f"{img_hash}.{extension}"
                
                # Save the image
                img_path = os.path.join(self.image_dir, img_filename)
                with open(img_path, 'wb') as f:
                    f.write(img_data)
                
                logger.info(f"Downloaded {img_url} ({width}x{height}, {size_bytes/1024:.1f}KB) to {img_path}")
                self.downloaded_count += 1
                return True
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Attempt {attempt+1}/{max_retries} failed for {img_url}: {e}")
                if attempt < max_retries - 1:
                    # Exponential backoff
                    wait_time = 2 ** attempt
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed to download {img_url} after {max_retries} attempts")
                    return False
                
            except Exception as e:
                logger.error(f"Error downloading image {img_url}: {e}")
                return False
    
    def extract_images_from_page(self, url: str) -> List[str]:
        """Extract all image URLs from a page with expanded detection patterns."""
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {url}: HTTP {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            img_urls = []
            
            # 1. Standard image tags
            for img in soup.find_all('img'):
                # Check regular src attribute
                src = img.get('src')
                if src:
                    img_urls.append(src)
                
                # Check for ALL data attributes that might contain images
                for attr in img.attrs:
                    if attr.startswith('data-') and ('src' in attr or 'image' in attr):
                        data_src = img.get(attr)
                        if data_src:
                            img_urls.append(data_src)
                
                # Check srcset attribute for responsive images
                srcset = img.get('srcset')
                if srcset:
                    # Extract URLs from srcset format: "url1 1x, url2 2x, ..."
                    for src_item in srcset.split(','):
                        src_parts = src_item.strip().split(' ')
                        if src_parts and src_parts[0]:
                            img_urls.append(src_parts[0])
            
            # 2. Background images in CSS (both inline and in style tags)
            # Style tags
            for style in soup.find_all('style'):
                if style.string:
                    # Extract URLs from background-image: url(...)
                    urls = re.findall(r'background-image:\s*url\([\'"]?([^\'"()]+)[\'"]?\)', style.string)
                    img_urls.extend(urls)
                    
                    # Also check for background: with url() function
                    urls = re.findall(r'background:\s*[^;]*url\([\'"]?([^\'"()]+)[\'"]?\)', style.string)
                    img_urls.extend(urls)
            
            # Inline styles
            for element in soup.find_all(attrs={'style': True}):
                style = element['style']
                # Background-image property
                urls = re.findall(r'background-image:\s*url\([\'"]?([^\'"()]+)[\'"]?\)', style)
                img_urls.extend(urls)
                
                # Background property
                urls = re.findall(r'background:\s*[^;]*url\([\'"]?([^\'"()]+)[\'"]?\)', style)
                img_urls.extend(urls)
            
            # 3. Look for JSON data that might contain image URLs
            scripts = soup.find_all('script', {'type': 'application/json'}) + soup.find_all('script', {'type': 'text/javascript'})
            for script in scripts:
                if script.string:
                    # Look for patterns that might be image URLs in JSON data
                    urls = re.findall(r'https?://[^"\']+\.(jpg|jpeg|png|gif|webp)', script.string)
                    for match in urls:
                        # Extract just the URL part (the full match without the extension group)
                        full_url = re.search(r'(https?://[^"\']+\.{})'.format(match), script.string)
                        if full_url:
                            img_urls.append(full_url.group(1))
            
            # 4. Look for common gallery structures
            gallery_containers = soup.find_all('div', class_=lambda c: c and any(term in c.lower() for term in ['gallery', 'slider', 'carousel']))
            for container in gallery_containers:
                # Check all a tags that might link to larger images
                for a in container.find_all('a', href=True):
                    href = a['href']
                    if re.search(r'\.(jpg|jpeg|png|gif|webp)$', href, re.IGNORECASE):
                        img_urls.append(href)
            
            return img_urls
        except Exception as e:
            logger.error(f"Error extracting images from {url}: {e}")
            return []
    
    def extract_links_from_page(self, url: str) -> List[str]:
        """Extract all links from a page."""
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {url}: HTTP {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            links = []
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                full_url = urljoin(url, href)
                
                if self.is_valid_url(full_url):
                    links.append(full_url)
            
            return links
            
        except Exception as e:
            logger.error(f"Error extracting links from {url}: {e}")
            return []
    
    def crawl(self):
        """Start crawling from the base URL."""
        self.visited_urls = set()
        self.image_urls = set()
        self.downloaded_count = 0
        
        # First, identify priority pages
        self._identify_priority_pages(self.base_url)
        
        # Process the landing page first
        self._process_url(self.base_url, is_priority=True)
        
        # Then process priority pages
        for url in self.priority_pages:
            if url not in self.visited_urls and len(self.visited_urls) < self.max_pages:
                self._process_url(url, is_priority=True)
        
        return self.downloaded_count
    
    def _identify_priority_pages(self, url):
        """Identify priority pages like rooms, suites, facilities, etc."""
        try:
            # Fetch the page
            logger.info(f"Scanning for priority pages at {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for links that might be relevant for a hotel website
            priority_keywords = [
                'room', 'suite', 'accommodation', 'stay', 'lodging',
                'facility', 'amenity', 'service', 'spa', 'restaurant',
                'dining', 'gallery', 'photo', 'image', 'tour',
                'about', 'location', 'contact'
            ]
            
            # Find all links
            for link in soup.find_all('a'):
                href = link.get('href')
                if not href:
                    continue
                
                # Get the link text
                link_text = link.text.lower().strip()
                
                # Check if the link or its text contains priority keywords
                is_priority = any(keyword in link_text or keyword in href.lower() for keyword in priority_keywords)
                
                if is_priority:
                    absolute_url = urljoin(url, href)
                    
                    # Only follow links to the same domain
                    if urlparse(absolute_url).netloc == self.domain:
                        logger.info(f"Found priority page: {absolute_url}")
                        self.priority_pages.append(absolute_url)
        
        except Exception as e:
            logger.error(f"Error identifying priority pages at {url}: {e}")
    
    def _process_url(self, url, is_priority=False):
        """Process a URL to find images and links."""
        # Check if we've reached the maximum number of pages
        if len(self.visited_urls) >= self.max_pages:
            return
        
        # Skip if already visited
        if url in self.visited_urls:
            return
        
        # Mark as visited
        self.visited_urls.add(url)
        
        try:
            # Fetch the page
            logger.info(f"Fetching {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find all images
            for img in soup.find_all('img'):
                img_url = img.get('src')
                if not img_url:
                    continue
                
                # Check if this is likely a content image (not an icon or decoration)
                width = img.get('width')
                height = img.get('height')
                
                # Skip small images that are likely icons
                if width and height and int(width) < 100 and int(height) < 100:
                    continue
                
                # Check for common icon/decoration patterns in the URL
                if any(pattern in img_url.lower() for pattern in ['icon', 'logo', 'button', 'bg-', 'background']):
                    continue
                
                # Add the image URL
                absolute_url = urljoin(url, img_url)
                self.image_urls.add(absolute_url)
                
                # Also check for data-src attribute (lazy loading)
                data_src = img.get('data-src')
                if data_src:
                    absolute_data_src = urljoin(url, data_src)
                    self.image_urls.add(absolute_data_src)
            
            # Look for background images in style attributes
            for element in soup.find_all(lambda tag: tag.has_attr('style')):
                style = element['style']
                if 'background-image' in style:
                    # Extract URL from background-image: url(...)
                    match = re.search(r'background-image:\s*url\([\'"]?([^\'"]+)[\'"]?\)', style)
                    if match:
                        bg_url = match.group(1)
                        absolute_bg_url = urljoin(url, bg_url)
                        self.image_urls.add(absolute_bg_url)
            
            # If this is a priority page, also look for links to other priority pages
            if is_priority:
                # Find all links to follow
                for link in soup.find_all('a'):
                    href = link.get('href')
                    if not href:
                        continue
                    
                    # Get the link text
                    link_text = link.text.lower().strip()
                    
                    # Check if the link or its text contains priority keywords
                    priority_keywords = [
                        'room', 'suite', 'accommodation', 'stay', 'lodging',
                        'facility', 'amenity', 'service', 'spa', 'restaurant',
                        'dining', 'gallery', 'photo', 'image', 'tour'
                    ]
                    
                    is_priority_link = any(keyword in link_text or keyword in href.lower() for keyword in priority_keywords)
                    
                    if is_priority_link:
                        absolute_url = urljoin(url, href)
                        
                        # Only follow links to the same domain
                        if urlparse(absolute_url).netloc == self.domain and absolute_url not in self.visited_urls:
                            # Add a delay to be nice to the server
                            time.sleep(self.delay)
                            self._process_url(absolute_url, is_priority=True)
        
        except Exception as e:
            logger.error(f"Error processing {url}: {e}")
        
        # Download all found images
        self._download_images()
    
    def _download_images(self):
        """Download all images in the image_urls set."""
        # Use a thread pool to download images concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # Submit download tasks
            future_to_url = {executor.submit(self._download_image, url): url for url in self.image_urls}
            
            # Process completed tasks
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error downloading {url}: {e}")
        
        # Clear the set of image URLs
        self.image_urls = set()
    
    def _download_image(self, url):
        """Download and save an image if it meets the criteria."""
        try:
            # Fetch the image
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                return
            
            # Check file size
            file_size_kb = len(response.content) / 1024
            if file_size_kb < self.min_size_kb:
                return
            
            # Open the image to check dimensions
            img = Image.open(BytesIO(response.content))
            width, height = img.size
            
            # Check dimensions
            if width < self.min_width or height < self.min_height:
                return
            
            # Generate a filename based on the URL
            filename = os.path.basename(urlparse(url).path)
            if not filename or '.' not in filename:
                # Use a hash of the URL if the filename is invalid
                filename = f"image_{hash(url) % 10000}.jpg"
            
            # Save the image
            file_path = os.path.join(self.image_dir, filename)
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Downloaded {url} to {file_path} ({width}x{height}, {file_size_kb:.1f}KB)")
            self.downloaded_count += 1
            
        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")

    def extract_images_with_browser(self, url):
        """Extract images using a headless browser to render JavaScript."""
        try:
            # This requires selenium to be installed: pip install selenium webdriver-manager
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            
            logger.info(f"Using headless browser to extract images from {url}")
            
            # Set up headless browser
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument(f"user-agent={self.headers['User-Agent']}")
            
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
            driver.get(url)
            
            # Wait for JavaScript to load
            time.sleep(5)
            
            # Extract images from rendered page
            img_elements = driver.find_elements("tag name", "img")
            img_urls = []
            
            for img in img_elements:
                src = img.get_attribute("src")
                if src:
                    img_urls.append(src)
                
                # Also check data attributes
                for attr in ['data-src', 'data-original', 'data-lazy-src', 'data-url']:
                    data_src = img.get_attribute(attr)
                    if data_src:
                        img_urls.append(data_src)
            
            # Get background images
            script = """
            return Array.from(document.querySelectorAll('*')).
            filter(e => window.getComputedStyle(e).backgroundImage.includes('url')).
            map(e => window.getComputedStyle(e).backgroundImage.replace(/url\\(['"](.+?)['"]/g, '$1'));
            """
            bg_images = driver.execute_script(script)
            img_urls.extend(bg_images)
            
            driver.quit()
            logger.info(f"Found {len(img_urls)} images using headless browser")
            return img_urls
            
        except Exception as e:
            logger.error(f"Error extracting images with browser from {url}: {e}")
            return []

def extract_url_from_structured_json(json_file_path: str) -> str:
    """Extract website URL and ID from a structured JSON file."""
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract the unique ID from the structured JSON
        unique_id = data.get('id')
        if not unique_id:
            # If no ID exists, use the filename without extension
            unique_id = os.path.splitext(os.path.basename(json_file_path))[0]
            logger.warning(f"No ID found in JSON, using filename: {unique_id}")
        
        # Extract the website URL from the structured JSON
        website_url = data.get('property', {}).get('contact', {}).get('website')
        
        if website_url:
            logger.info(f"Extracted URL from {os.path.basename(json_file_path)} with ID: {unique_id}: {website_url}")
            return website_url, unique_id
        else:
            # Try to find any URL in the JSON
            json_str = json.dumps(data)
            import re
            url_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'
            urls = re.findall(url_pattern, json_str)
            
            if urls:
                website_url = urls[0]  # Take the first URL found
                logger.info(f"Found alternative URL in {os.path.basename(json_file_path)} with ID: {unique_id}: {website_url}")
                return website_url, unique_id
            else:
                logger.warning(f"No URL found in {json_file_path} with ID: {unique_id}")
                return "", unique_id
            
    except Exception as e:
        logger.error(f"Error extracting URL from {json_file_path}: {e}")
        # Return empty URL and filename as ID
        return "", os.path.splitext(os.path.basename(json_file_path))[0]

def get_all_json_files(directory: str) -> List[str]:
    """Get all JSON files in the specified directory."""
    return glob.glob(os.path.join(directory, "*.json"))

def download_images_from_url(url, min_width, min_height, min_size, delay, max_pages):
    """Download images from a URL using the image downloader script."""
    try:
        # Check if images.py exists
        script_path = os.path.join("hotelWebsiteScraping", "images.py")
        if not os.path.exists(script_path):
            logger.error(f"Image downloader script not found at {script_path}")
            return False
        
        # Run the image downloader script
        cmd = [
            sys.executable,
            script_path,
            "--min-width", str(min_width),
            "--min-height", str(min_height),
            "--min-size", str(min_size),
            "--delay", str(delay),
            "--max-pages", str(max_pages),
            "--url", url
        ]
        
        logger.info(f"Running image downloader for {url}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info(f"Successfully downloaded images from {url}")
            
            # Try to find the output directory
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            image_dir = os.path.join("MCP", "data", "images", domain.replace(".", "_"))
            
            if os.path.exists(image_dir):
                logger.info(f"Images saved to {image_dir}")
                return True
            else:
                logger.info("Images downloaded successfully")
                return True
        else:
            logger.error(f"Error downloading images: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error running image downloader: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Download images from a website and its subpages")
    parser.add_argument("--url", "-u", help="URL of the website to crawl")
    parser.add_argument("--id", help="Unique ID to use for the image folder")
    parser.add_argument("--min-width", type=int, default=800, help="Minimum image width in pixels")
    parser.add_argument("--min-height", type=int, default=600, help="Minimum image height in pixels")
    parser.add_argument("--min-size", type=int, default=50, help="Minimum image size in KB")
    parser.add_argument("--delay", "-d", type=float, default=1.0, help="Delay between requests in seconds")
    parser.add_argument("--max-pages", "-m", type=int, default=20, help="Maximum number of pages to crawl")
    parser.add_argument("--json-file", help="Path to structured JSON file to extract URL and ID from")
    
    args = parser.parse_args()
    
    unique_id = args.id
    url = args.url
    
    # If JSON file is provided, extract URL and ID from it
    if args.json_file:
        extracted_url, extracted_id = extract_url_from_structured_json(args.json_file)
        if not url and extracted_url:
            url = extracted_url
            logger.info(f"Using URL from JSON file: {url}")
        if not unique_id:
            unique_id = extracted_id
            logger.info(f"Using ID from JSON file: {unique_id}")
    
    # Get URL from command line or prompt if still not available
    if not url:
        url = input("Enter the URL to download images from: ").strip()
    
    if not url:
        logger.error("No URL provided. Exiting.")
        return
    
    # Add http:// if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
        logger.info(f"Added https:// to URL: {url}")
    
    downloader = ImageDownloader(
        base_url=url,
        unique_id=unique_id,
        min_width=args.min_width,
        min_height=args.min_height,
        min_size_kb=args.min_size,
        delay=args.delay,
        max_pages=args.max_pages
    )
    
    logger.info(f"Starting image download from {url}")
    logger.info(f"Minimum dimensions: {args.min_width}x{args.min_height} pixels")
    logger.info(f"Minimum size: {args.min_size} KB")
    
    start_time = time.time()
    image_count = downloader.crawl()
    elapsed_time = time.time() - start_time
    
    logger.info(f"Download complete! Downloaded {image_count} images in {elapsed_time:.1f} seconds")
    logger.info(f"Images saved to {downloader.image_dir}")

if __name__ == "__main__":
    main()
