from __future__ import annotations
import httpx
import json
import logging
import time
from typing import Dict, Any, Optional
from python.helpers.tool import Tool, Response
from python.helpers.oauth_helper import OAuthManager
from python.helpers.circuit_breaker import (
    get_circuit_breaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
)

logger = logging.getLogger("tool.zoho_crm")

# Circuit breaker config tuned for Zoho API rate limits
ZOHO_CB_CONFIG = CircuitBreakerConfig(
    failure_threshold=3,         # Trip after 3 consecutive failures
    success_threshold=2,         # Reset after 2 successes in half-open
    timeout=60.0,                # Wait 60s before retrying after trip
    use_exponential_backoff=True,
    max_timeout=300.0,           # Max backoff 5 minutes
    backoff_multiplier=2.0,
)


def _sanitize_json_data(data: Any) -> Any:
    """Preprocess and validate JSON data before sending to Zoho API.
    
    Handles common LLM issues:
    - String data that needs JSON parsing
    - Nested string-encoded JSON
    - Null/empty field cleanup
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            # Try cleaning common LLM artifacts
            cleaned = data.strip()
            for prefix in ("```json", "```"):
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            try:
                data = json.loads(cleaned.strip())
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON data: {data[:200]}")
    
    if isinstance(data, dict):
        # Remove null/empty values that Zoho rejects
        return {k: v for k, v in data.items() if v is not None and v != ""}
    
    return data


class ZohoCRM(Tool):
    """Zoho CRM tool with circuit breaker protection and entity context tracking.
    
    Enhanced with:
    - Circuit breaker pattern for API resilience (Issue #812)
    - Entity context tracking between sequential operations
    - JSON response preprocessing and validation
    """

    # Class-level entity context for tracking between operations
    _last_entity_context: Dict[str, Any] = {}

    async def execute(self, **kwargs) -> Response:
        action = self.args.get("action")
        if not action:
            return Response(
                message="Error: Missing 'action' argument. "
                        "Valid actions are: search_leads, create_lead, update_lead, delete_lead, get_context.",
                break_loop=False,
            )

        # Return current entity context for chaining operations
        if action == "get_context":
            if self._last_entity_context:
                return Response(
                    message=f"Current entity context:\n{json.dumps(self._last_entity_context, indent=2)}",
                    break_loop=False,
                )
            return Response(message="No entity context available yet.", break_loop=False)

        # Get or create circuit breaker for Zoho API
        breaker = await get_circuit_breaker("zoho-crm-api", ZOHO_CB_CONFIG)

        # Check circuit breaker state before making API call
        if not await breaker.can_execute():
            retry_info = breaker.get_status()
            return Response(
                message=f"⚠️ Zoho API circuit breaker is OPEN (too many failures). "
                        f"Retry in {retry_info.get('time_until_retry', 0):.0f}s. "
                        f"Stats: {retry_info['stats']['failed_calls']} failures, "
                        f"{retry_info['stats']['consecutive_failures']} consecutive.",
                break_loop=False,
            )

        try:
            oauth = OAuthManager(service_name="ZOHO", context=self.agent.context)
            access_token = await oauth.get_access_token()
            
            api_domain = "https://www.zohoapis.com"
            base_url = f"{api_domain}/crm/v7"
            
            headers = {
                "Authorization": f"Zoho-oauthtoken {access_token}",
                "Content-Type": "application/json"
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                if action == "search_leads":
                    criteria = self.args.get("criteria", "")
                    if criteria and not criteria.startswith("(") and not criteria.endswith(")"):
                        criteria = f"({criteria})"
                    
                    if not criteria or criteria == "()":
                        criteria = "(Created_Time:greater_than:2000-01-01T00:00:00+00:00)"
                    
                    params = {"criteria": criteria}
                    resp = await client.get(f"{base_url}/Leads/search", headers=headers, params=params)
                    
                elif action == "create_lead":
                    data = self.args.get("data")
                    if not data:
                        return Response(message="Error: Missing 'data' argument for create_lead.", break_loop=False)
                    data = _sanitize_json_data(data)
                    payload = {"data": [data]}
                    resp = await client.post(f"{base_url}/Leads", headers=headers, json=payload)
                    
                elif action == "update_lead":
                    lead_id = self.args.get("lead_id")
                    # Support entity context: use last_lead_id if not explicitly provided
                    if not lead_id and self._last_entity_context.get("last_lead_id"):
                        lead_id = self._last_entity_context["last_lead_id"]
                    data = self.args.get("data")
                    if not lead_id or not data:
                        return Response(
                            message="Error: Missing 'lead_id' or 'data' argument for update_lead.",
                            break_loop=False,
                        )
                    data = _sanitize_json_data(data)
                    payload = {"data": [data]}
                    resp = await client.put(f"{base_url}/Leads/{lead_id}", headers=headers, json=payload)
                    
                elif action == "delete_lead":
                    lead_id = self.args.get("lead_id")
                    if not lead_id and self._last_entity_context.get("last_lead_id"):
                        lead_id = self._last_entity_context["last_lead_id"]
                    if not lead_id:
                        return Response(
                            message="Error: Missing 'lead_id' argument for delete_lead.",
                            break_loop=False,
                        )
                    resp = await client.delete(f"{base_url}/Leads/{lead_id}", headers=headers)
                    
                else:
                    return Response(message=f"Error: Unknown action '{action}'.", break_loop=False)

                # Record success with circuit breaker
                await breaker.record_success()

                # Handle response
                if resp.status_code == 204:
                    return Response(
                        message="Success: No content returned (consistent with empty results).",
                        break_loop=False,
                    )
                
                result = resp.json()
                
                # Update entity context for chaining
                self._update_entity_context(action, result)
                
                if resp.status_code >= 400:
                    # Record API errors (4xx/5xx) as circuit breaker failures
                    if resp.status_code >= 500:
                        await breaker.record_failure(
                            Exception(f"Zoho API {resp.status_code}")
                        )
                    return Response(
                        message=f"Zoho API Error ({resp.status_code}): {json.dumps(result, indent=2)}",
                        break_loop=False,
                    )
                
                return Response(
                    message=f"Success: {json.dumps(result, indent=2)}",
                    break_loop=False,
                )

        except (httpx.TimeoutException, httpx.ConnectError, ConnectionError) as e:
            # Network errors → record circuit breaker failure
            await breaker.record_failure(e)
            logger.warning(f"Zoho API network error (circuit breaker notified): {e}")
            return Response(
                message=f"Error: Zoho API network error: {str(e)}. "
                        f"Circuit breaker status: {breaker.state.value}",
                break_loop=False,
            )
        except Exception as e:
            # Other errors → record failure
            await breaker.record_failure(e)
            return Response(
                message=f"Error executing Zoho CRM action: {str(e)}",
                break_loop=False,
            )

    def _update_entity_context(self, action: str, result: dict):
        """Update entity context for chaining between operations."""
        self._last_entity_context["last_action"] = action
        self._last_entity_context["last_result_time"] = time.time()
        
        # Extract lead ID from create/search results for use in subsequent operations
        if isinstance(result, dict) and "data" in result:
            data_list = result["data"]
            if isinstance(data_list, list) and data_list:
                first_item = data_list[0]
                if isinstance(first_item, dict):
                    lead_id = first_item.get("id") or first_item.get("details", {}).get("id")
                    if lead_id:
                        self._last_entity_context["last_lead_id"] = str(lead_id)
                        self._last_entity_context["last_entity"] = first_item
