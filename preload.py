import asyncio
from python.helpers import runtime, whisper, settings
from python.helpers.print_style import PrintStyle
from python.helpers import kokoro_tts
import python.models as models


async def preload():
    try:
        set = settings.get_settings()

        # preload embedding model
        async def preload_embedding():
            if set.get("embed_model_provider", "").lower() == "huggingface":
                try:
                    name = set.get("embed_model_name", "all-MiniLM-L6-v2")
                    PrintStyle().info(f"Pre-loading embedding model: {name}")
                    # Use the new LiteLLM-based model system
                    emb_mod = models.get_embedding_model("huggingface", name)
                    # Warming up the model
                    await emb_mod.aembed_query("warmup")
                    return True
                except Exception as e:
                    PrintStyle().warning(f"Embedding warmup skipped (non-critical): {type(e).__name__}: {str(e)[:200]}")

        # 3. Post-preload Database Maintenance (Issue #377)
        async def maintenance_check():
            try:
                from python.helpers.database_client import DatabaseClient
                db = DatabaseClient.get_instance()
                # Non-blocking vacuum check inside the task
                await db.vacuum()
            except Exception as e:
                PrintStyle().error(f"Error in database maintenance check: {e}")

        # async tasks to preload - only keep embedding for startup stability
        # STT and TTS are situational and lazy-loaded on first use
        tasks = [
            preload_embedding(),
            maintenance_check()
        ]

        await asyncio.gather(*[t for t in tasks if t is not None], return_exceptions=True)
        PrintStyle().success("Preload sequence completed (Embeddings warmed up)")
    except Exception as e:
        PrintStyle().error(f"Critical error in preload: {e}")


# preload transcription model
if __name__ == "__main__":
    print("DEBUG: Preload starting...", flush=True)
    PrintStyle().print("Running preload...")
    print("DEBUG: Initializing runtime...", flush=True)
    runtime.initialize()
    
    # Sync secrets to environment for sub-processes
    try:
        from python.helpers.secrets_helper import get_default_secrets_manager
        get_default_secrets_manager().sync_to_environ()
        print("DEBUG: Secrets synced to environment", flush=True)
    except Exception as e:
        print(f"DEBUG: Failed to sync secrets: {e}", flush=True)

    print("DEBUG: Runtime initialized.", flush=True)
    print("DEBUG: Starting asyncio run...", flush=True)
    asyncio.run(preload())
    print("DEBUG: Preload script finished.", flush=True)
