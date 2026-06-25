from __future__ import annotations
import os
import sys
import asyncio
import logging
from typing import List

# Setup path to include /agix
sys.path.append("/agix")

from python.helpers.memory import Memory, MyFaiss
from python.helpers import files
from python.helpers.projects import PROJECT_META_DIR, LEGACY_PROJECT_META_DIR

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sync_memories")

SEED_BASE = "/seed"
TARGET_BASE = "/agix"

async def merge_indices(seed_dir: str, target_dir: str, subdir_name: str):
    """Merges FAISS index from seed_dir into target_dir."""
    try:
        os.nice(10) # Lower priority for background sync
    except OSError:
        pass

    seed_index_path = os.path.join(seed_dir, "index.faiss")
    target_index_path = os.path.join(target_dir, "index.faiss")

    if not os.path.exists(seed_index_path):
        return

    if not os.path.exists(target_index_path):
        # Fresh start, just copy the whole directory
        logger.info(f"Seed only found for {subdir_name}. Copying fresh.")
        os.makedirs(target_dir, exist_ok=True)
        # Use cp -r to be safe
        os.system(f"cp -r {seed_dir}/* {target_dir}/")
        return

    # Check if seed is actually newer
    if os.path.exists(target_index_path) and os.path.getmtime(target_index_path) >= os.path.getmtime(seed_index_path):
        logger.info(f"Target index is up to date for {subdir_name}. Skipping merge.")
        return

    # Both exist, need to merge
    logger.info(f"Merging indices for {subdir_name}...")
    try:
        # We need an embedder to load/save if we were using Memory class, 
        # but MyFaiss (FAISS) can load directly if we have the right components.
        # Actually, to merge properly we should load them as FAISS objects.
        
        # Load target (internal)
        # We use a dummy embedder or the one from config? 
        # FAISS.load_local requires an embeddings object.
        import python.models as models
        from python.helpers import settings
        
        # Correctly access settings using get_settings()
        current_settings = settings.get_settings()
        provider = current_settings.get("embed_model_provider", "huggingface")
        name = current_settings.get("embed_model_name", "sentence-transformers/all-MiniLM-L6-v2")
        embedder = models.get_embedding_model(provider, name)
        
        target_db = MyFaiss.load_local(target_dir, embedder, allow_dangerous_deserialization=True)
        seed_db = MyFaiss.load_local(seed_dir, embedder, allow_dangerous_deserialization=True)
        
        # Get all docs from seed
        seed_docs = seed_db.docstore._dict.values()
        
        # Check for duplicates? Simple check by content for now
        existing_contents = {doc.page_content for doc in target_db.docstore._dict.values()}
        new_docs = [doc for doc in seed_docs if doc.page_content not in existing_contents]
        
        if new_docs:
            logger.info(f"Adding {len(new_docs)} new memories to {subdir_name}")
            target_db.add_documents(new_docs)
            target_db.save_local(target_dir)
        else:
            logger.info(f"No new memories found to merge for {subdir_name}")

    except Exception as e:
        logger.error(f"Failed to merge {subdir_name}: {e}")

async def main():
    # 1. Handle Global memories
    seed_mem_default = os.path.join(SEED_BASE, "memory/default")
    target_mem_default = os.path.join(TARGET_BASE, "memory/default")
    await merge_indices(seed_mem_default, target_mem_default, "default")

    # 2. Handle Project memories
    seed_projects = os.path.join(SEED_BASE, "projects")
    if os.path.exists(seed_projects):
        for p_name in os.listdir(seed_projects):
            # Try new convention first, fall back to legacy
            seed_p_mem = os.path.join(seed_projects, p_name, PROJECT_META_DIR, "memory")
            if not os.path.exists(seed_p_mem):
                seed_p_mem = os.path.join(seed_projects, p_name, LEGACY_PROJECT_META_DIR, "memory")
            target_p_mem = os.path.join(TARGET_BASE, "usr/projects", p_name, PROJECT_META_DIR, "memory")
            if not os.path.exists(target_p_mem):
                legacy_target = os.path.join(TARGET_BASE, "usr/projects", p_name, LEGACY_PROJECT_META_DIR, "memory")
                if os.path.exists(legacy_target):
                    target_p_mem = legacy_target
            
            if os.path.exists(seed_p_mem):
                await merge_indices(seed_p_mem, target_p_mem, f"projects/{p_name}")

if __name__ == "__main__":
    asyncio.run(main())
