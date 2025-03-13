import requests
from bs4 import BeautifulSoup
from datetime import datetime
import logging
import time
import random
from rate_limit_helper import RateLimiter

# Create a rate limiter instance to limit requests to tournament pages
rate_limiter = RateLimiter(requests_per_minute=10)

def fetch_registration_details(url):
    """
    Fetch tournament registration details with rate limiting
    
    This function uses a rate limiter to avoid being blocked by the website.
    """
    # Wait if needed to respect the rate limit
    rate_limiter.wait_if_needed()
    
    try:
        # Add a random user agent to seem more like a real browser
        headers = {
            'User-Agent': random.choice([
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0'
            ]),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        # Add a small delay after the request
        time.sleep(random.uniform(1, 2))
        
        # Check if we got a successful response
        if response.status_code != 200:
            logging.warning(f"Got status code {response.status_code} from {url}")
            return {
                "closing_text": "N/A",
                "closing_date": None,
                "registrants": 0,
                "capacity": 0
            }
            
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract registration closing date
        cutoff_div = soup.select_one("div.cutoff span")
        closing_date = None
        closing_text = "N/A"
        if cutoff_div:
            closing_text = cutoff_div.text.strip()
            try:
                # Extract date from text like "Online registration closes January 23, 2025 at 6:00pm EST"
                if "closes " in closing_text:
                    date_part = closing_text.split("closes ")[1].split(" at")[0]
                    closing_date = datetime.strptime(date_part, "%B %d, %Y")
                    # remove "online registration closes" from closing_text
                    if "Online registration closes " in closing_text:
                        closing_text = closing_text.split("Online registration closes ")[1]
                elif "closed" in closing_text.lower():
                    # Handle case where registration is already closed
                    closing_text = "Registration closed"
            except (IndexError, ValueError) as e:
                logging.warning(f"Failed to parse closing date from '{closing_text}': {e}")
                closing_date = None  # Handle invalid or missing date format

        # Extract registrants and capacity
        registrants = 0
        capacity = 0
        
        # Try first with the registered players link
        registered_span = soup.find("a", string=lambda x: x and "Registered Players" in x)
        if registered_span:
            try:
                # Extract numbers from "80 / 216" in the span text
                reg_span = registered_span.find("span")
                if reg_span:
                    registered_text = reg_span.text.strip()
                    if " / " in registered_text:
                        registrants, capacity = map(int, registered_text.split(" / "))
                    elif registered_text.isdigit():
                        registrants = int(registered_text)
            except (AttributeError, ValueError, IndexError) as e:
                logging.warning(f"Failed to parse registrants/capacity from registered players link: {e}")
        
        # If not found, try with the registration section
        if registrants == 0 and capacity == 0:
            reg_section = soup.select_one(".registration-section")
            if reg_section:
                try:
                    reg_span = reg_section.select_one(".registrants")
                    if reg_span:
                        reg_text = reg_span.text.strip()
                        if "Players:" in reg_text:
                            reg_text = reg_text.split("Players:")[1].strip()
                            if "/" in reg_text:
                                registrants, capacity = map(int, reg_text.split("/"))
                            elif reg_text.isdigit():
                                registrants = int(reg_text)
                except (AttributeError, ValueError, IndexError) as e:
                    logging.warning(f"Failed to parse registrants/capacity from registration section: {e}")

        return {
            "closing_text": closing_text,
            "closing_date": closing_date,
            "registrants": registrants,
            "capacity": capacity 
        }
    except Exception as e:
        logging.error(f"Error fetching tournament details: {e}")
        return {
            "closing_text": "N/A",
            "closing_date": None,
            "registrants": 0,
            "capacity": 0
        }
