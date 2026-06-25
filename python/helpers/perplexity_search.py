from __future__ import annotations

from openai import OpenAI
import python.models as models

def perplexity_search(query:str, model_name="sonar",api_key=None,base_url="https://api.perplexity.ai"):    
    api_key = api_key or models.get_api_key("perplexity")

    client = OpenAI(api_key=api_key, base_url=base_url)
        
    messages = [
    # NOTE: Perplexity docs recommend NO system prompt for sonar models.
    # Anti-hallucination for PII is handled by:
    # 1. flag_aggregator_hallucination() in search_engine.py (output detection)
    # 2. Researcher prompt rules (tool output verification + PII workaround)
    # Do NOT add a system prompt here — it degrades search quality.
    {
        "role": "user",
        "content": (
            query
        ),
    },
    ]
    
    response = client.chat.completions.create(
        model=model_name,
        messages=messages, # type: ignore
    )
    content = response.choices[0].message.content or ""

    # Extract citation URLs from the Perplexity API response.
    # The 'sonar' model returns citations in a separate array (not inline).
    citations = []
    try:
        citations = getattr(response, 'citations', None) or []
        if not citations and hasattr(response, 'model_extra') and response.model_extra:
            citations = response.model_extra.get('citations', [])
    except Exception:
        pass

    if citations:
        content += "\n\nSources:"
        for i, url in enumerate(citations, 1):
            content += f"\n[{i}] {url}"

    return content