import time
import random
import logging
from functools import wraps

# Default delay ranges in seconds
DEFAULT_MIN_DELAY = 2
DEFAULT_MAX_DELAY = 5

def randomized_delay(min_delay=DEFAULT_MIN_DELAY, max_delay=DEFAULT_MAX_DELAY):
    """Decorator to add a randomized delay before function execution"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = random.uniform(min_delay, max_delay)
            logging.info(f"Rate limit: Adding {delay:.2f}s delay before {func.__name__}")
            time.sleep(delay)
            return func(*args, **kwargs)
        return wrapper
    return decorator

def add_random_delay(min_delay=DEFAULT_MIN_DELAY, max_delay=DEFAULT_MAX_DELAY):
    """Add a random delay between operations to simulate human behavior"""
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)

class RateLimiter:
    """Class to manage request rates to avoid hitting rate limits"""
    
    def __init__(self, requests_per_minute=10):
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60 / requests_per_minute
        self.last_request_time = 0
    
    def wait_if_needed(self):
        """Wait if we're making requests too quickly"""
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        
        if elapsed < self.min_interval:
            wait_time = self.min_interval - elapsed
            logging.info(f"Rate limiting: Waiting {wait_time:.2f}s to respect rate limit")
            time.sleep(wait_time)
            
        self.last_request_time = time.time()
