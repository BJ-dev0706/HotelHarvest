# Hotel Scraping

A comprehensive web scraping and data enrichment pipeline for hotel websites that extracts structured data, images, and enriches it with AI.

## Overview

This project provides an end-to-end solution for extracting valuable information from hotel websites. It intelligently crawls hotel sites, extracts structured data, downloads high-quality images, and uses AI to enrich and normalize the data into a consistent JSON schema.

## Features

- **Intelligent Web Crawler**: Navigates hotel websites, prioritizing pages with valuable content
- **Structured Data Extraction**: Converts unstructured HTML to structured markdown and JSON
- **Smart Image Collection**: Downloads high-quality hotel images with size and quality filters
- **AI Enrichment Pipeline**: Uses LLMs to normalize and enrich extracted data
- **Support for Chain Hotels**: Correctly handles single-property and multi-property hotel sites
- **Modular Architecture**: Easy to extend with new capabilities

## Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/BJ-dev0706/HotelHarvest.git
cd HotelHarvest
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root with your API keys and settings:

```
# OpenAI API settings
OPENAI_API_KEY=your_api_key_here
MODEL_NAME=gpt-4o-mini
LLM_TEMPERATURE=0.2

# Crawler settings
CRAWL_DELAY=1.0
MAX_BOOKING_URLS=5
```

## Usage

### Basic Usage

To scrape a single hotel website:

```bash
python main.py --url https://www.bestwestern-quartier-latin.com/
```

This will:
1. Crawl the website and extract markdown content
2. Structure the data into a standardized JSON format
3. Download and process all relevant images

### Advanced Options

```bash
python main.py --url https://www.bestwestern-quartier-latin.com/en/hotel --delay 2.0 --min-width 1024 --min-height 768 --min-size 100
```

Parameters:
- `--delay`: Time between requests in seconds (default: 1.0)
- `--min-width`: Minimum image width in pixels (default: 800)
- `--min-height`: Minimum image height in pixels (default: 600)
- `--min-size`: Minimum image size in KB (default: 50)
- `--max-pages`: Maximum number of pages to crawl (default: 20)

### Batch Processing

To process multiple hotel websites:

```bash
python main.py --url-file hotel_urls.txt
```

Where `hotel_urls.txt` contains one URL per line.

## Project Structure

- `main.py` - Main entry point with website crawling logic
- `structure_info.py` - Extracts structured information using AI
- `images.py` - Handles image extraction and downloading
- `requirements.txt` - Project dependencies

## Output

The scraper generates data in the following structure:

```
MCP/
└── data/
    ├── markdown/
    │   └── {hotel_id}.md
    ├── structured/
    │   └── {hotel_id}.json
    └── images/
        └── {hotel_id}/
            ├── image1.jpg
            ├── image2.jpg
            └── metadata.json
```

## Roadmap

- [ ] Implement image auto-tagging with vision models
- [ ] Add support for reviews scraping from OTAs
- [ ] Improve chain website detection and handling
- [ ] Create a web dashboard for monitoring scraping progress
- [ ] Add support for international sites with translation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

