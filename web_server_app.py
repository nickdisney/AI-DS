# web_server_app.py
# Flask application logic for the web interface.

import os
import traceback
from flask import Flask, request, render_template, send_from_directory, jsonify, redirect, url_for, flash
import ollama

# Use new app structure for imports
from app import config
from app import utils
from app.sd_api import client as sd_client
from app.file_system import manager as file_system_manager # <<< Import file manager

web_context = {'generation_queue': None}

# --- Helper Functions ---

def _fetch_ollama_models_list():
    """Fetches list of installed Ollama model names using the ollama library."""
    model_names = []
    try:
        models_data = ollama.list()
        potential_models = models_data.get('models', [])
        if isinstance(potential_models, list):
             for m in potential_models:
                 model_id = None
                 if isinstance(m, dict): model_id = m.get('model', m.get('name'))
                 elif hasattr(m, 'model'): model_id = m.model
                 elif hasattr(m, 'name'): model_id = m.name
                 if model_id and isinstance(model_id, str): model_names.append(model_id)
        print(f"DEBUG: Found Ollama models: {model_names}")
        return sorted(list(set(model_names)))
    except Exception as e:
        print(f"❌ Error fetching Ollama models: {e}")
        traceback.print_exc()
        return []

# --- App Factory ---

def create_flask_app(generation_queue_ref):
    """Creates and configures the Flask application."""
    app = Flask(__name__, template_folder='templates') # Assumes templates/ next to main_web.py
    app.secret_key = os.urandom(24)

    web_context['generation_queue'] = generation_queue_ref
    print(f"DEBUG: Flask app created. Generation Queue reference set: {web_context['generation_queue'] is not None}")

    # --- Flask Routes ---

    @app.route("/", methods=["GET"])
    def index():
        """Renders the main web page."""
        print("DEBUG: Accessing index route '/'")
        try:
            # Fetch dynamic data
            print("DEBUG: Fetching Ollama models...")
            available_ollama_models = _fetch_ollama_models_list()
            print("DEBUG: Fetching SD checkpoints via client...")
            available_sd_checkpoints = sd_client.fetch_sd_checkpoints()

            print("DEBUG: Finding speaker files via file manager...")
            # <<< Use file manager to find speakers >>>
            speaker_paths = file_system_manager.find_wav_files(config.SPEAKER_SAMPLE_DIR)
            available_speakers = sorted([os.path.basename(p) for p in speaker_paths])
            print(f"DEBUG: Speakers found: {len(available_speakers)}")

            available_characters = list(config.CHARACTERS.keys())
            print("DEBUG: Data fetched, preparing template render...")

            return render_template(
                "index.html",
                speakers=available_speakers,
                characters=available_characters,
                default_character=config.DEFAULT_CHARACTER,
                ollama_models=available_ollama_models,
                default_ollama_model=config.OLLAMA_MODEL_NARRATION,
                sd_checkpoints=available_sd_checkpoints,
                default_sd_vae=config.SD_DEFAULT_VAE if config.SD_DEFAULT_VAE else "",
                default_negative_prompt=config.SD_DEFAULT_NEGATIVE_PROMPT
            )
        except Exception as e:
            print(f"❌ Error rendering index page: {e}")
            traceback.print_exc()
            return f"<h1>Error loading page</h1><p>An unexpected error occurred. Please check the server logs.</p><pre>{traceback.format_exc()}</pre>", 500


    @app.route("/generate", methods=["POST"])
    def generate():
        """Handles the generation request from the web form."""
        print("DEBUG: Received POST request on /generate")
        gen_queue = web_context.get('generation_queue')
        if not gen_queue:
             print("ERROR: Generation queue not available in /generate route.")
             flash("Generation service is currently unavailable. Please try again later.", "error")
             return redirect(url_for('index'))

        try:
            prompt = request.form.get("prompt", "").strip()
            count_str = request.form.get("count", "1")
            speaker_name = request.form.get("speaker")
            mode = request.form.get("mode", "Story")
            character = request.form.get("character")
            selected_ollama_model = request.form.get("ollama_model")
            selected_sd_model = request.form.get("sd_checkpoint")
            selected_sd_vae = request.form.get("sd_vae", "").strip()
            negative_prompt = request.form.get("negative_prompt", "").strip()
            lora_syntax = request.form.get("lora_syntax", "").strip()

            try: count = int(count_str); count = max(1, min(count, 50))
            except ValueError: flash("Invalid count specified. Using 1.", "warning"); count = 1

            if not speaker_name: flash("No speaker voice selected.", "error"); return redirect(url_for('index'))
            speaker_path = os.path.join(config.SPEAKER_SAMPLE_DIR, speaker_name)
            if not os.path.exists(speaker_path): flash(f"Speaker file not found: {speaker_name}", "error"); return redirect(url_for('index'))
            if not selected_ollama_model: flash("No Ollama model selected.", "error"); return redirect(url_for('index'))

            final_sd_model = selected_sd_model if selected_sd_model else None
            final_sd_vae = selected_sd_vae if selected_sd_vae else None
            final_lora = lora_syntax if lora_syntax else None
            final_neg_prompt = negative_prompt if negative_prompt else config.SD_DEFAULT_NEGATIVE_PROMPT
            final_character = character if mode == "Conversation" else None

            job = {
                "count": count, "custom_prompt": prompt, "speaker_wav": speaker_path,
                "mode": mode, "character": final_character, "ollama_model": selected_ollama_model,
                "sd_checkpoint": final_sd_model, "sd_vae": final_sd_vae,
                "sd_negative_prompt": final_neg_prompt, "lora_syntax": final_lora,
            }
            print(f"DEBUG: Prepared job dictionary: {job}")

            gen_queue.put(job)
            print(f"DEBUG: Job submitted to queue. Queue size approx: {gen_queue.qsize()}")
            flash(f"Generation job ({count} item{'s' if count > 1 else ''}) submitted successfully!", "success")

        except Exception as e:
            print(f"❌ Error submitting generation job: {e}")
            traceback.print_exc()
            flash(f"Error submitting job: {e}", "error")

        return redirect(url_for('index'))


    # --- Static File Serving ---
    @app.route("/audio/<path:filename>")
    def serve_audio(filename):
        print(f"DEBUG: Serving audio: {filename}")
        return send_from_directory(config.AUDIO_OUTPUT_DIR, filename, as_attachment=False)

    @app.route("/image/<path:filename>")
    def serve_image(filename):
        print(f"DEBUG: Serving image: {filename}")
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
             return "Invalid file type", 400
        return send_from_directory(config.IMAGE_OUTPUT_DIR, filename)

    @app.route("/text/<path:filename>")
    def serve_text(filename):
        print(f"DEBUG: Serving text: {filename}")
        if not filename.lower().endswith('.txt'): return "Invalid file type", 400
        try:
             return send_from_directory(config.TEXT_OUTPUT_DIR, filename, mimetype='text/plain; charset=utf-8')
        except FileNotFoundError: print(f"ERROR: Text file not found: {filename}"); return "Text file not found.", 404
        except Exception as e: print(f"ERROR: Error serving text file {filename}: {e}"); traceback.print_exc(); return "Error serving file.", 500


    # --- API Endpoints ---
    @app.route("/files/list")
    def list_files_api():
        """API endpoint to get a list of generated files using file manager."""
        print("DEBUG: API call to /files/list")
        try:
             # <<< Use file manager function >>>
             audio_files_data = file_system_manager.list_generated_files()
             print(f"DEBUG: Returning {len(audio_files_data)} files from API via file manager.")
             return jsonify(audio_files_data)
        except Exception as e:
             print(f"❌ Error listing files via API: {e}")
             traceback.print_exc()
             return jsonify({"error": f"Failed to list files: {e}"}), 500

    return app