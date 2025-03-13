"""
This module provides async worker functionality to fetch tournament details
without blocking the Discord event loop.
"""
import asyncio
import logging
import time
import random
from datetime import datetime
from fetch_registration_details import fetch_registration_details

# Default capacity to use when the actual capacity is unknown
DEFAULT_CAPACITY = 72
# Percentage threshold at which to consider a tournament "filling up"
FILLING_THRESHOLD = 75

class DetailWorker:
    """Worker class to fetch tournament details asynchronously"""
    
    def __init__(self, executor, max_concurrent=2):
        """
        Initialize the worker
        
        Args:
            executor: ThreadPoolExecutor to use for background tasks
            max_concurrent: Maximum number of concurrent detail requests
        """
        self.executor = executor
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    async def get_tournament_details(self, tournament):
        """
        Fetch additional details for a tournament asynchronously
        
        Args:
            tournament: Tournament dictionary with at least a URL field
        
        Returns:
            Dictionary with additional tournament details
        """
        async with self.semaphore:
            loop = asyncio.get_running_loop()
            details = await loop.run_in_executor(
                self.executor, 
                lambda: fetch_registration_details(tournament["url"])
            )
            return details
    
    async def enrich_tournaments(self, tournaments):
        """
        Process a list of tournaments and enrich with details
        
        Args:
            tournaments: List of tournament dictionaries
        
        Returns:
            Lists of tournaments needing special notifications
        """
        closing_soon = []
        filling_up = []
        
        # First pre-filter tournaments that need details
        eligible_tournaments = []
        
        for tournament in tournaments:
            should_check_closing = False
            should_check_filling = False
            
            # Check if we should check for closing soon
            try:
                if tournament["date"] != "N/A":
                    date_obj = datetime.strptime(tournament["date"], "%m/%d/%Y")
                    days_until_tournament = (date_obj - datetime.now()).days
                    should_check_closing = days_until_tournament <= 14 and not tournament.get("registration_closing_sent", False)
            except (ValueError, TypeError) as e:
                logging.warning(f"Date parsing error for {tournament['name']}: {e}")
            
            # Check if there are enough registrants to potentially be "filling up"
            # Use either the actual capacity or DEFAULT_CAPACITY
            tournament_capacity = tournament.get("capacity", 0) or DEFAULT_CAPACITY
            fill_percentage = (tournament["registrants"] / tournament_capacity) * 100 if tournament_capacity > 0 else 0
            
            # If it's already at least 50% full, we should check it
            should_check_filling = fill_percentage >= 50 and not tournament.get("registration_filling_sent", False)
            
            # Only fetch details when needed and registration is open
            if tournament["url"] != "N/A" and tournament.get("registration_open", True) and (should_check_closing or should_check_filling):
                eligible_tournaments.append((tournament, should_check_closing, should_check_filling))
        
        # Process eligible tournaments in batches to avoid overwhelming the server
        batch_size = 5
        for i in range(0, len(eligible_tournaments), batch_size):
            batch = eligible_tournaments[i:i+batch_size]
            tasks = []
            
            # Create tasks for this batch
            for tournament, check_closing, check_filling in batch:
                tasks.append((tournament, check_closing, check_filling, self.get_tournament_details(tournament)))
            
            # Wait a moment between batches to avoid rate limiting
            if i > 0:
                await asyncio.sleep(2.0)
            
            # Process results as they complete
            for tournament, check_closing, check_filling, task in tasks:
                try:
                    details = await task
                    tournament.update(details)  # Add fetched details to the tournament dictionary
                    
                    # Check for "closing soon"
                    if check_closing and details["closing_date"]:
                        days_left = (details["closing_date"] - datetime.now()).days
                        if days_left < 7:
                            closing_soon.append(tournament)
                            tournament["registration_closing_sent"] = True
                    
                    # Check for "filling up"
                    if check_filling:
                        # Use either the fetched capacity or the capacity from the listing, or DEFAULT_CAPACITY
                        capacity = details.get("capacity", tournament.get("capacity", 0)) or DEFAULT_CAPACITY
                        
                        # Use the larger of the registrant counts
                        registrants = max(details.get("registrants", 0), tournament.get("registrants", 0))
                        
                        # Calculate percentage and check if it's filling up
                        if capacity > 0:  # Avoid division by zero
                            fill_percentage = (registrants / capacity) * 100
                            if fill_percentage >= FILLING_THRESHOLD:
                                # Update the tournament with the latest numbers
                                tournament["registrants"] = registrants
                                tournament["capacity"] = capacity
                                filling_up.append(tournament)
                                tournament["registration_filling_sent"] = True
                                logging.info(f"Tournament filling up: {tournament['name']} - {registrants}/{capacity} ({fill_percentage:.1f}%)")
                
                except Exception as e:
                    logging.error(f"Error processing details for {tournament['name']}: {e}")
        
        logging.info(f"Processed details for {len(eligible_tournaments)} tournaments, found {len(closing_soon)} closing soon and {len(filling_up)} filling up")
        return closing_soon, filling_up
