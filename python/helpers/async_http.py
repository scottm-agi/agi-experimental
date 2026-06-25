"""
Async HTTP Client with Circuit Breaker and Retry Support

Provides a centralized async HTTP client that integrates:
- Circuit breaker pattern for external service protection
- Exponential backoff with jitter on retries
- Configurable timeouts
- Proper async/await semantics

Usage:
    from python.helpers.async_http import AsyncHTTPClient, HTTPClientConfig
    
    client = AsyncHTTPClient("forgejo_api")
    response = await client.get("https://api.example.com/issues")
    
    # Or with custom config
    config = HTTPClientConfig(timeout=60.0, max_retries=5)
    client = AsyncHTTPClient("github_api", config=config)
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union
from contextlib import asynccontextmanager

import httpx

from python.helpers.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
)

logger = logging.getLogger(__name__)


@dataclass
class HTTPClientConfig:
    """Configuration for async HTTP client."""
    
    # Request settings
    timeout: float = 30.0                    # Request timeout in seconds
    connect_timeout: float = 10.0            # Connection timeout
    
    # Retry settings
    max_retries: int = 3                     # Maximum retry attempts
    retry_on_status: tuple = (429, 500, 502, 503, 504)  # Status codes to retry
    initial_delay: float = 1.0               # Initial retry delay
    max_delay: float = 60.0                  # Maximum retry delay
    backoff_multiplier: float = 2.0          # Exponential backoff multiplier
    jitter_factor: float = 0.2               # Random jitter (±20%)
    
    # Circuit breaker settings
    use_circuit_breaker: bool = True
    circuit_failure_threshold: int = 5       # Failures before opening
    circuit_success_threshold: int = 3       # Successes to close
    circuit_timeout: float = 30.0            # Recovery timeout
    circuit_max_timeout: float = 300.0       # Max circuit timeout
    
    def calculate_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        base = self.initial_delay * (self.backoff_multiplier ** attempt)
        capped = min(base, self.max_delay)
        jitter = random.uniform(-self.jitter_factor, self.jitter_factor)
        return max(0.1, capped * (1 + jitter))


class AsyncHTTPClient:
    """
    Async HTTP client with circuit breaker and retry support.
    
    Provides a unified interface for making async HTTP requests with:
    - Automatic retries with exponential backoff
    - Circuit breaker pattern for failure isolation
    - Proper timeout handling
    - Retry-After header parsing
    """
    
    # Class-level registry of clients for reuse
    _clients: Dict[str, "AsyncHTTPClient"] = {}
    _lock = asyncio.Lock()
    
    def __init__(
        self,
        service_name: str,
        config: Optional[HTTPClientConfig] = None,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize async HTTP client.
        
        Args:
            service_name: Unique name for this service (used for circuit breaker)
            config: Client configuration
            base_url: Optional base URL for all requests
            default_headers: Default headers to include in all requests
        """
        self.service_name = service_name
        self.config = config or HTTPClientConfig()
        self.base_url = base_url
        self.default_headers = default_headers or {}
        
        # Initialize circuit breaker if enabled
        self._circuit_breaker: Optional[CircuitBreaker] = None
        if self.config.use_circuit_breaker:
            self._circuit_breaker = CircuitBreaker(
                name=f"http_{service_name}",
                config=CircuitBreakerConfig(
                    failure_threshold=self.config.circuit_failure_threshold,
                    success_threshold=self.config.circuit_success_threshold,
                    timeout=self.config.circuit_timeout,
                    max_timeout=self.config.circuit_max_timeout,
                    use_exponential_backoff=True,
                    jitter_factor=self.config.jitter_factor,
                )
            )
    
    @classmethod
    async def get_or_create(
        cls,
        service_name: str,
        config: Optional[HTTPClientConfig] = None,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> "AsyncHTTPClient":
        """Get existing client or create new one."""
        async with cls._lock:
            if service_name not in cls._clients:
                cls._clients[service_name] = cls(
                    service_name=service_name,
                    config=config,
                    base_url=base_url,
                    default_headers=default_headers,
                )
            return cls._clients[service_name]
    
    @asynccontextmanager
    async def _get_client(self):
        """Context manager for httpx client with proper timeout config."""
        timeout = httpx.Timeout(
            timeout=self.config.timeout,
            connect=self.config.connect_timeout,
        )
        # Only pass base_url if it's set (avoids httpx type validation error)
        client_kwargs = {
            "timeout": timeout,
            "follow_redirects": True,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        
        async with httpx.AsyncClient(**client_kwargs) as client:
            yield client
    
    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        Make an HTTP request with retry and circuit breaker support.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            url: Request URL (relative if base_url is set)
            **kwargs: Additional arguments passed to httpx
            
        Returns:
            httpx.Response object
            
        Raises:
            CircuitBreakerError: If circuit is open
            httpx.HTTPStatusError: If request fails after all retries
            httpx.TimeoutException: If request times out after all retries
        """
        # Merge headers
        headers = {**self.default_headers, **kwargs.pop("headers", {})}
        kwargs["headers"] = headers
        
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                # Check circuit breaker before request
                if self._circuit_breaker:
                    if not await self._circuit_breaker.can_execute():
                        raise CircuitBreakerError(
                            f"Circuit breaker {self.service_name} is OPEN"
                        )
                
                async with self._get_client() as client:
                    response = await client.request(method, url, **kwargs)
                    
                    # Check for retryable status codes
                    if response.status_code in self.config.retry_on_status:
                        if attempt < self.config.max_retries:
                            delay = self._get_retry_delay(response, attempt)
                            logger.warning(
                                f"[{self.service_name}] {method} {url} returned {response.status_code}, "
                                f"retry {attempt + 1}/{self.config.max_retries} in {delay:.1f}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                        else:
                            # Record failure for circuit breaker
                            if self._circuit_breaker:
                                await self._circuit_breaker.record_failure(
                                    Exception(f"HTTP {response.status_code}")
                                )
                            response.raise_for_status()
                    
                    # Success - record for circuit breaker
                    if self._circuit_breaker:
                        await self._circuit_breaker.record_success()
                    
                    return response
                    
            except CircuitBreakerError:
                raise
            except httpx.TimeoutException as e:
                last_exception = e
                if self._circuit_breaker:
                    await self._circuit_breaker.record_failure(e)
                if attempt < self.config.max_retries:
                    delay = self.config.calculate_retry_delay(attempt)
                    logger.warning(
                        f"[{self.service_name}] {method} {url} timed out, "
                        f"retry {attempt + 1}/{self.config.max_retries} in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
            except httpx.ConnectError as e:
                last_exception = e
                if self._circuit_breaker:
                    await self._circuit_breaker.record_failure(e)
                if attempt < self.config.max_retries:
                    delay = self.config.calculate_retry_delay(attempt)
                    logger.warning(
                        f"[{self.service_name}] {method} {url} connection failed: {e}, "
                        f"retry {attempt + 1}/{self.config.max_retries} in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
            except Exception as e:
                last_exception = e
                if self._circuit_breaker:
                    await self._circuit_breaker.record_failure(e)
                # Don't retry on unexpected errors
                raise
        
        # All retries exhausted
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Request failed after {self.config.max_retries} retries")
    
    def _get_retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """Get retry delay, respecting Retry-After header if present."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self.config.calculate_retry_delay(attempt)
    
    # Convenience methods
    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Make a GET request."""
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Make a POST request."""
        return await self.request("POST", url, **kwargs)
    
    async def put(self, url: str, **kwargs) -> httpx.Response:
        """Make a PUT request."""
        return await self.request("PUT", url, **kwargs)
    
    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """Make a PATCH request."""
        return await self.request("PATCH", url, **kwargs)
    
    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """Make a DELETE request."""
        return await self.request("DELETE", url, **kwargs)
    
    def get_circuit_breaker_status(self) -> Optional[Dict[str, Any]]:
        """Get circuit breaker status for monitoring."""
        if self._circuit_breaker:
            return self._circuit_breaker.get_status()
        return None


# Pre-configured clients for common services
async def get_forgejo_client(
    base_url: str,
    token: Optional[str] = None,
) -> AsyncHTTPClient:
    """Get Forgejo API client with proper configuration."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    
    config = HTTPClientConfig(
        timeout=30.0,
        max_retries=3,
        circuit_failure_threshold=5,
        circuit_timeout=60.0,
    )
    
    return await AsyncHTTPClient.get_or_create(
        service_name="forgejo",
        config=config,
        base_url=base_url,
        default_headers=headers,
    )


async def get_github_client(
    token: Optional[str] = None,
) -> AsyncHTTPClient:
    """Get GitHub API client with proper configuration."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    config = HTTPClientConfig(
        timeout=30.0,
        max_retries=3,
        circuit_failure_threshold=5,
        circuit_timeout=60.0,
    )
    
    return await AsyncHTTPClient.get_or_create(
        service_name="github",
        config=config,
        base_url="https://api.github.com",
        default_headers=headers,
    )


async def get_railway_client(
    token: Optional[str] = None,
) -> AsyncHTTPClient:
    """Get Railway API client with proper configuration."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    config = HTTPClientConfig(
        timeout=60.0,  # Railway operations can be slow
        max_retries=3,
        circuit_failure_threshold=5,
        circuit_timeout=120.0,
    )
    
    return await AsyncHTTPClient.get_or_create(
        service_name="railway",
        config=config,
        base_url="https://backboard.example.com/graphql/v2",
        default_headers=headers,
    )
