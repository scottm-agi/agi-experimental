import collections
import os
import re
import json
import logging
from flask import Blueprint, jsonify, request
from python.helpers import files
from python.agent import AgentContextType

logger = logging.getLogger(__name__)

prompts_bp = Blueprint('prompts', __name__)

def normalize_prompt(text):
    """Normalize prompt for grouping by removing extra whitespace and common filler."""
    if not text:
        return ""
    # Remove whitespace, non-alphanumeric (except some punctuation), and lowercase
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    # Remove trailing/leading punctuation
    text = text.strip('?!.,')
    return text

@prompts_bp.route('/prompts/common', methods=['GET', 'POST'])
def get_common_prompts():
    """
    Returns a list of commonly used prompts and golden prompts.
    Scans history and the prompts/ directory.
    """
    try:
        counts = collections.Counter()
        originals = {}
        
        # Scan history for frequently used prompts
        chats_folder = "tmp/chats"
        abs_chats_folder = files.get_abs_path(chats_folder)
        
        folders = []
        if os.path.exists(abs_chats_folder):
            folders = [f for f in os.listdir(abs_chats_folder) if os.path.isdir(os.path.join(abs_chats_folder, f))]
        
        for folder_name in folders:
            chat_file = os.path.join(abs_chats_folder, folder_name, "chat.json")
            if not os.path.exists(chat_file):
                continue
                
            try:
                content = files.read_file(chat_file)
                if not content:
                    continue
                data = json.loads(content)
                
                # Only look at USER type chats
                if data.get("type") != AgentContextType.USER.value:
                    continue
                    
                for agent_data in data.get("agents", []):
                    # We usually care about the main agent (number 0)
                    if agent_data.get("number") != 0:
                        continue
                        
                    history_str = agent_data.get("history", "")
                    if not history_str:
                        continue
                        
                    history_data = json.loads(history_str)
                    for msg in history_data:
                        if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "h":
                            raw = msg[1].strip() if isinstance(msg[1], str) else ""
                            if not raw:
                                continue
                                
                            normalized = normalize_prompt(raw)
                            if normalized and len(normalized) > 3:
                                counts[normalized] += 1
                                if normalized not in originals:
                                    originals[normalized] = collections.Counter()
                                originals[normalized][raw] += 1
            except Exception as e:
                logger.debug(f"Error processing chat file {folder_name}: {e}")
                continue
        
        # Compile results
        top_prompts = []
        # Return top 20 most frequent prompt patterns
        for normalized, count in counts.most_common(20):
            display_version = originals[normalized].most_common(1)[0][0]
            top_prompts.append({
                "prompt": display_version,
                "count": count
            })
            
        # Also include Golden Prompts from the prompts directory
        prompts_dir = "prompts"
        abs_prompts_dir = files.get_abs_path(prompts_dir)
        if os.path.exists(abs_prompts_dir):
            for filename in os.listdir(abs_prompts_dir):
                if filename.startswith("golden_") and filename.endswith(".md"):
                    file_path = os.path.join(abs_prompts_dir, filename)
                    try:
                        content = files.read_file(file_path)
                        match = re.search(r'# Golden Prompt\n\n(.*)', content, re.DOTALL)
                        if match:
                            prompt_text = match.group(1).strip()
                            if prompt_text:
                                top_prompts.insert(0, {
                                    "prompt": prompt_text,
                                    "count": 100, # Priority count
                                    "golden": True
                                })
                    except Exception as e:
                        logger.debug(f"Error reading golden prompt {filename}: {e}")

        # Limit to top 5 for dropdown/UI but preserve order
        # Display priorities first
        return jsonify({
            "success": True,
            "prompts": top_prompts[:20] # Or whatever UI limit is needed
        })
    except Exception as e:
        logger.error(f"Failed to generate common prompts ranking: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@prompts_bp.route('/prompts/golden/save', methods=['POST'])
def save_golden_prompt():
    """
    Saves a prompt as a golden prompt file (.md) in the prompts directory.
    """
    try:
        data = request.get_json()
        prompt_text = data.get('prompt', '').strip()
        
        if not prompt_text:
            return jsonify({"success": False, "error": "Prompt text is required"}), 400
            
        from python.helpers.hashing import content_hash_short
        normalized = normalize_prompt(prompt_text)
        h = content_hash_short(normalized, length=8)
        filename = f"golden_{h}.md"
        
        prompts_dir = "prompts"
        abs_prompts_dir = files.get_abs_path(prompts_dir)
        
        if not os.path.exists(abs_prompts_dir):
            os.makedirs(abs_prompts_dir, exist_ok=True)
            
        file_path = os.path.join(abs_prompts_dir, filename)
        
        content = f"# Golden Prompt\n\n{prompt_text}\n"
        files.write_file(file_path, content)
        
        logger.info(f"Saved golden prompt to {file_path}")
        
        return jsonify({
            "success": True,
            "filename": filename,
            "message": "Golden prompt saved successfully"
        })
    except Exception as e:
        logger.error(f"Failed to save golden prompt: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@prompts_bp.route('/prompts/common/delete', methods=['POST'])
def delete_prompt():
    """
    Deletes a golden prompt by filename (preferred) or by hash lookup (fallback).
    """
    try:
        data = request.get_json()
        filename = data.get('filename', '').strip()
        prompt_text = data.get('prompt', '').strip()
        
        if not filename and not prompt_text:
            return jsonify({"success": False, "error": "Filename or prompt text is required"}), 400
        
        prompts_dir = "prompts"
        abs_prompts_dir = files.get_abs_path(prompts_dir)
        
        # Strategy 1: Delete by filename (reliable)
        if filename and filename.startswith("golden_") and filename.endswith(".md"):
            file_path = os.path.join(abs_prompts_dir, filename)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted golden prompt file {file_path}")
                # Invalidate the golden prompts cache
                globals()['_golden_prompts_cache'] = None
                globals()['_golden_prompts_cache_time'] = 0
                return jsonify({
                    "success": True, 
                    "message": "Golden prompt deleted successfully"
                })
        
        # Strategy 2: Hash-based fallback (for backwards compatibility)
        if prompt_text:
            from python.helpers.hashing import content_hash_short
            normalized = normalize_prompt(prompt_text)
            h = content_hash_short(normalized, length=8)
            hash_filename = f"golden_{h}.md"
            file_path = os.path.join(abs_prompts_dir, hash_filename)
            
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted golden prompt file {file_path} (hash fallback)")
                # Invalidate the golden prompts cache
                globals()['_golden_prompts_cache'] = None
                globals()['_golden_prompts_cache_time'] = 0
                return jsonify({
                    "success": True, 
                    "message": "Golden prompt deleted successfully"
                })

        # If we get here, file wasn't found by either method
        logger.warning(f"Golden prompt not found for deletion: filename={filename}, prompt_hash={h if prompt_text else 'N/A'}")
        return jsonify({
            "success": False,
            "message": "Golden prompt file not found"
        }), 404
            
    except Exception as e:
        logger.error(f"Failed to delete prompt: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@prompts_bp.route('/prompts/golden/list', methods=['GET', 'POST'])
def list_golden_prompts():
    """
    Returns only the prompts saved as golden files.
    Optimized with memory caching to reduce disk I/O latency.
    """
    try:
        # Simple memory cache with 60s TTL
        import time
        global _golden_prompts_cache, _golden_prompts_cache_time
        if '_golden_prompts_cache' not in globals():
            _golden_prompts_cache = None
            _golden_prompts_cache_time = 0
            
        current_time = time.time()
        prompts_dir = "prompts"
        abs_prompts_dir = files.get_abs_path(prompts_dir)
        
        # Invalidate cache if folder mtime changed or TTL expired
        dir_mtime = 0
        if os.path.exists(abs_prompts_dir):
            dir_mtime = os.path.getmtime(abs_prompts_dir)
            
        if (_golden_prompts_cache is not None and 
            current_time - _golden_prompts_cache_time < 60 and 
            _golden_prompts_cache_dir_mtime == dir_mtime):
            return jsonify({
                "success": True,
                "prompts": _golden_prompts_cache,
                "cached": True
            })

        golden_prompts = []
        if os.path.exists(abs_prompts_dir):
            for filename in os.listdir(abs_prompts_dir):
                if filename.startswith("golden_") and filename.endswith(".md"):
                    try:
                        file_path = os.path.join(abs_prompts_dir, filename)
                        content = files.read_file(file_path)
                        # Extract prompt from content which starts with "# Golden Prompt\n\n"
                        prompt_text = content.replace("# Golden Prompt\n\n", "").strip()
                        if prompt_text:
                            # Use file modification time for sorting
                            mtime = os.path.getmtime(file_path)
                            golden_prompts.append({
                                "prompt": prompt_text,
                                "count": 1,
                                "golden": True,
                                "filename": filename,
                                "updated_at": mtime
                            })
                    except Exception as e:
                        logger.debug(f"Error reading golden prompt {filename}: {e}")
        
        # Sort by updated_at desc
        golden_prompts.sort(key=lambda x: x['updated_at'], reverse=True)
        
        # Update cache
        globals()['_golden_prompts_cache'] = golden_prompts
        globals()['_golden_prompts_cache_time'] = current_time
        globals()['_golden_prompts_cache_dir_mtime'] = dir_mtime
        
        return jsonify({
            "success": True,
            "prompts": golden_prompts
        })
    except Exception as e:
        logger.error(f"Failed to list golden prompts: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

def register_prompts_endpoints(app):
    """Register prompts endpoints with the Flask app."""
    app.register_blueprint(prompts_bp, url_prefix='/api')
    logger.info("Prompts API endpoints registered")
