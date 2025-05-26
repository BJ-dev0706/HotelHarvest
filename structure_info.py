import os
import json
import glob
from typing import List, Dict, Any, Optional
import logging
from dotenv import load_dotenv
import argparse
import pathlib
import re
import requests  # Use requests instead of OpenAI client

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_json_files(directory: str, specific_files: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Load JSON files from the specified directory, optionally filtering by filename."""
    json_files = glob.glob(os.path.join(directory, "*.json"))
    
    # Filter files if specific ones are requested
    if specific_files:
        # Convert to set of basenames for faster lookup
        specific_basenames = {os.path.basename(f) for f in specific_files}
        json_files = [f for f in json_files if os.path.basename(f) in specific_basenames]
    
    logger.info(f"Found {len(json_files)} JSON files to process in {directory}")
    
    # Return list of tuples with (file_path, data)
    result = []
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                result.append((file_path, data))
                logger.info(f"Loaded {file_path} successfully")
        except Exception as e:
            logger.error(f"Error loading {file_path}: {e}")
    
    return result

def structure_content(file_path: str, content_data: Dict[str, Any]) -> str:
    """Use direct API call to OpenAI instead of using clients with version conflicts."""
    unique_id = os.path.splitext(os.path.basename(file_path))[0]
    
    # Get API key and model configuration
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        raise ValueError("OPENAI_API_KEY environment variable not set or invalid")
    
    model_name = os.getenv("MODEL_NAME", "gpt-4o-mini")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    
    logger.info(f"Using model: {model_name} with temperature: {temperature}")
    
    # Create the system prompt
    system_prompt = """
    You are an expert data extractor for accommodation websites. Your task is to extract and structure 
    information from the provided website content into a detailed JSON format.
    
    IMPORTANT: Translate ALL information into English, regardless of the original language of the content.
    
    CRITICAL: You MUST return ONLY valid, parseable JSON. Do not include any explanations, markdown formatting, 
    or code blocks. The output should be a raw JSON object that can be directly parsed by json.loads().
    
    Extract the following information (if available):
    
    1. Basic Information:
       - Property name
       - Description       
       - Stars (if available)
       - Address (full address with postal code)
       - Phone number(s)
       - Email address
       - Website URL
       - Social media links
    
    2. Rooms/Accommodations (for each type):
       - Room type/name
       - Description
       - Price
       - Size (in sq ft/m²)
       - Maximum occupancy
       - Bed configuration (e.g., 1 king, 2 queens)
       - Amenities in room
       - Views/location
       - Accessibility features
    
    3. Property Amenities:
       - Parking information
       - WiFi details
       - Pool/spa facilities
       - Fitness center
       - Restaurant/bar
       - Business facilities
       - Laundry services
       - Pet policy
       - Accessibility features
    
    4. Policies:
       - Check-in/check-out times
       - Cancellation policy
       - Payment methods
       - Deposit requirements
       - Age restrictions
       - Pet policy details
    
    5. Location Information:
       - Nearby attractions
       - Distance to key locations (airport, downtown, etc.)
       - Transportation options
       - Neighborhood description
    
    Return ONLY a well-structured JSON object with all the extracted information. Use null for missing information.
    Ensure the JSON is properly formatted and includes as much detail as possible from the source material.
    
    It is better to include more information than too little - be comprehensive and thorough in your extraction.
    
    Use exactly this JSON structure (filling in the actual data):
    
    {
      "id": "HOTEL_ID",
      "property": {
        "name": "Property Name",
        "description": "Full property description...",
        "stars": "Number of stars (e.g., 5)",
        "address": {
          "street": "Street address",
          "city": "City",
          "state": "State/Province",
          "postalCode": "Postal/Zip code",
          "country": "Country"
        },
        "contact": {
          "phone": ["Primary phone", "Alternative phone (if available)"],
          "email": "Email address",
          "website": "Website URL"
        },
        "miscellaneous": [
          "Special feature 1 (e.g., Panoramic rooftop views)",
          "Special feature 2 (e.g., Michelin-starred restaurant)",
          "Special feature 3 (e.g., Award-winning spa)"
        ]
      },
      "rooms": [
        {
          "type": "Room type/name",
          "description": "Detailed room description",
          "price": "Price per night",
          "size": "Size in sq ft/m²",
          "max_occupancy": "Maximum number of guests",
          "bedConfiguration": "Detailed bed information",
          "amenities": ["Amenity 1", "Amenity 2", "..."],
          "views": "View description",
          "accessibility": ["Accessibility feature 1", "..."]
        }
      ],
      "amenities": {
        "parking": "Parking information",
        "wifi": "WiFi details",
        "poolSpa": ["Pool/spa facility 1", "..."],
        "fitness": "Fitness center details",
        "dining": ["Restaurant/bar 1", "..."],
        "business": ["Business facility 1", "..."],
        "laundry": "Laundry service details",
        "petPolicy": "Pet policy information",
        "accessibility": ["Accessibility feature 1", "..."],
        "other": ["Other amenity 1", "..."]
      },
      "policies": {
        "checkIn": "Check-in time/procedure",
        "checkOut": "Check-out time/procedure",
        "cancellation": "Cancellation policy details",
        "payment": ["Accepted payment method 1", "..."],
        "deposit": "Deposit requirements",
        "ageRestrictions": "Age restriction details",
        "petDetails": "Detailed pet policy"
      },
      "location": {
        "nearbyAttractions": ["Attraction 1", "..."],
        "distances": [
          { "place": "Airport", "distance": "Distance in km/miles" },
          { "place": "Downtown", "distance": "Distance in km/miles" }
        ],
        "transportation": ["Transportation option 1", "..."],
        "neighborhood": "Neighborhood description"
      }
    }
    
    FINAL CHECK: Verify that your output is valid JSON before submitting. Do not include any text outside the JSON object.
    Do not include the triple backticks or json keyword. Return ONLY the raw JSON object.
    """
    
    # Convert the data to a string representation
    content_str = json.dumps(content_data, indent=2)
    
    # Truncate if too large
    if len(content_str) > 30000:
        logger.warning("Content too large, truncating to 30,000 characters")
        content_str = content_str[:30000] + "...[truncated]"
    
    # Direct API call using requests
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_str}
        ],
        "temperature": temperature
    }
    
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload
        )
        
        if response.status_code != 200:
            error_msg = f"OpenAI API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        # Extract the result
        result = response.json()["choices"][0]["message"]["content"]
        
        # Validate JSON before returning
        try:
            # Try to parse the result as JSON
            parsed_json = json.loads(result)
            
            # Ensure the ID is included
            if "id" not in parsed_json:
                parsed_json["id"] = unique_id
                result = json.dumps(parsed_json)
                
            logger.info(f"Successfully structured content for {os.path.basename(file_path)} with ID: {unique_id}")
            return result
        except json.JSONDecodeError as e:
            # If parsing fails, try to fix common issues
            logger.warning(f"Generated content is not valid JSON. Attempting to fix: {e}")
            
            # Remove any markdown code block markers
            result = re.sub(r'```json\s*', '', result)
            result = re.sub(r'```\s*', '', result)
            
            # Try parsing again
            try:
                parsed_json = json.loads(result)
                
                # Ensure the ID is included
                if "id" not in parsed_json:
                    parsed_json["id"] = unique_id
                    result = json.dumps(parsed_json)
                    
                logger.info("Fixed JSON formatting issues")
                return result
            except json.JSONDecodeError:
                # If still invalid, return error JSON
                logger.error("Could not fix JSON formatting issues")
                error_json = {
                    "id": unique_id,
                    "error": "The LLM did not generate valid JSON",
                    "property": {
                        "name": "Error processing content",
                        "description": "The content could not be properly structured.",
                        "contact": {
                            "website": extract_website_from_content(content_data)
                        }
                    }
                }
                return json.dumps(error_json)
    except Exception as e:
        error_msg = f"Error structuring content: {e}"
        logger.error(error_msg)
        error_json = {
            "id": unique_id,
            "error": error_msg,
            "property": {
                "name": "Error processing content",
                "description": "An error occurred during processing.",
                "contact": {
                    "website": extract_website_from_content(content_data)
                }
            }
        }
        return json.dumps(error_json)

def extract_website_from_content(content_data: Dict[str, Any]) -> str:
    """Extract a website URL from the content data if possible."""
    try:
        # Convert to string and search for URLs
        content_str = json.dumps(content_data)
        import re
        url_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'
        urls = re.findall(url_pattern, content_str)
        
        # Filter for likely hotel website URLs
        hotel_urls = [url for url in urls if any(term in url.lower() for term in 
                     ['hotel', 'resort', 'inn', 'accommodation', 'booking', 'stay', 'room'])]
        
        if hotel_urls:
            return hotel_urls[0]
        elif urls:
            return urls[0]
        else:
            return ""
    except:
        return ""

def save_structured_content(content: str, input_file_path: str, output_dir: str):
    """Save the structured content to a JSON file with the same base name as the input file."""
    os.makedirs(output_dir, exist_ok=True)
    
    # Get the original filename and use it for the output
    input_filename = os.path.basename(input_file_path)
    output_path = os.path.join(output_dir, input_filename)
    
    # Ensure the content is valid JSON
    try:
        # Parse the content to ensure it's valid JSON
        json_content = json.loads(content)
        
        # Write the formatted JSON to the output file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_content, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved structured content to {output_path}")
        return output_path
    except json.JSONDecodeError:
        # If the content isn't valid JSON, try to fix it or create a minimal valid JSON
        logger.error(f"Generated content is not valid JSON. Attempting to fix...")
        
        # Create a minimal valid JSON with error information
        fallback_json = {
            "error": "The LLM did not generate valid JSON",
            "property": {
                "name": "Error processing content",
                "description": "The content could not be properly structured."
            },
            "rawContent": content[:1000] + "..." if len(content) > 1000 else content
        }
        
        # Save the fallback JSON
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(fallback_json, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved fallback JSON to {output_path}")
        return output_path

def list_available_files(directory: str) -> List[str]:
    """List all JSON files in the directory."""
    json_files = glob.glob(os.path.join(directory, "*.json"))
    return [os.path.basename(f) for f in json_files]

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Structure accommodation website content")
    parser.add_argument("--files", "-f", nargs="+", help="Specific files to process (optional)")
    parser.add_argument("--all", "-a", action="store_true", help="Process all files")
    args = parser.parse_args()
    
    # Define paths
    input_dir = os.path.join("Scraper", "output")
    output_dir = os.path.join("Scraper", "structured")
    
    # Check if input directory exists
    if not os.path.exists(input_dir):
        logger.error(f"Input directory {input_dir} does not exist. Please run the scraper first.")
        return
    
    # If no arguments provided, show available files and prompt user
    if not args.files and not args.all:
        available_files = list_available_files(input_dir)
        
        if not available_files:
            logger.warning("No JSON files found in the input directory.")
            return
        
        print("Available files to process:")
        for i, file in enumerate(available_files):
            print(f"{i+1}. {file}")
        
        print("\nEnter the numbers of the files to process (comma-separated), or 'all' for all files:")
        user_input = input("> ").strip()
        
        if user_input.lower() == 'all':
            files_to_process = available_files
        else:
            try:
                # Parse user input as comma-separated list of numbers
                indices = [int(idx.strip()) - 1 for idx in user_input.split(',')]
                files_to_process = [available_files[idx] for idx in indices if 0 <= idx < len(available_files)]
            except (ValueError, IndexError):
                logger.error("Invalid input. Please run the script again.")
                return
    else:
        # Use command line arguments
        if args.all:
            files_to_process = list_available_files(input_dir)
        else:
            files_to_process = args.files
    
    # Load and process the selected JSON files
    file_data_pairs = load_json_files(input_dir, files_to_process)
    
    if not file_data_pairs:
        logger.warning("No files were loaded. Please check your selection.")
        return
    
    # Process each file individually
    for file_path, data in file_data_pairs:
        # Structure the content
        structured_content = structure_content(file_path, data)
        
        # Save the structured content to an individual file
        save_structured_content(structured_content, file_path, output_dir)
    
    logger.info("Content structuring complete!")

if __name__ == "__main__":
    main()
