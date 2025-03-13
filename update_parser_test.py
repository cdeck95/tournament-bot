import os
import logging
from bs4 import BeautifulSoup
from datetime import datetime
from script import parse_tournament_page

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def test_tournament_parser():
    """Test the tournament parser with the new HTML format"""
    # Load the sample HTML file
    html_file_path = os.path.join(os.path.dirname(__file__), "tournament_list.html")
    
    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as e:
        logging.error(f"Failed to read HTML file: {e}")
        return
    
    # Parse the tournaments
    tournaments = parse_tournament_page(html_content)
    
    # Log the results
    logging.info(f"Found {len(tournaments)} tournaments")
    for i, tournament in enumerate(tournaments[:5], 1):  # Show first 5 as examples
        logging.info(f"\nTournament {i}:")
        logging.info(f"Name: {tournament['name']}")
        logging.info(f"URL: {tournament['url']}")
        logging.info(f"Location: {tournament['location']}")
        logging.info(f"Date: {tournament['date']}")
        logging.info(f"Tier: {tournament['tier']}")
        logging.info(f"Registrants: {tournament['registrants']}")
        logging.info(f"Capacity: {tournament['capacity']}")
        logging.info(f"Registration Open: {tournament['registration_open']}")
    
    # Verify data correctness
    if tournaments:
        # Check a few specific values from the example data
        assert "TORONTO OPEN" in tournaments[0]["name"]
        assert "Toronto, ON" in tournaments[0]["location"]
        
        # Verify the Golden Horseshoe Open has the right registrants/capacity
        golden_horseshoe = next((t for t in tournaments if t["name"] == "Golden Horseshoe Open"), None)
        if golden_horseshoe:
            assert golden_horseshoe["registrants"] == 123
            assert golden_horseshoe["capacity"] == 120
            logging.info("\nAll assertions passed!")
        else:
            logging.warning("Couldn't find Golden Horseshoe Open for testing")
    else:
        logging.error("No tournaments were parsed!")

if __name__ == "__main__":
    test_tournament_parser()
