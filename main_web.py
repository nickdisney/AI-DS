# main_web.py
# Entry point script to initialize and launch the Flask Web application.

import os
import sys
import time
import torch
import queue
import threading
import traceback
import signal
import logging

# Add project root to sys.path FIRST
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
print(f"DEBUG: Added project root to sys.path: {project_root}")

# Import from our app structure
from app import config
# <<< Import new check functions >>>
from app import utils
from app.file_system import manager as file_system_manager
from app.worker.main import narrator_worker
from app.web.factory import create_flask_app # Import from correct location

# Import TTS specific classes ONLY here
try:
    from TTS.api import TTS
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import XttsAudioConfig, XttsArgs
    from TTS.config.shared_configs import BaseDatasetConfig
    TTS_AVAILABLE = True
except ImportError as e:
    log_msg = f"Error importing TTS components: {e}. Please ensure you have installed Coqui TTS: pip install TTS"
    try: logging.critical(log_msg)
    except NameError: print(f"❌ CRITICAL: {log_msg}")
    TTS_AVAILABLE = False

# Global variables
worker_thread = None
generation_queue = None
tts_instance = None
keep_running = True # For graceful shutdown signal

def setup_logging():
    """Configures logging for the web application."""
    # ... (logging setup unchanged) ...
    log_file = os.path.join(project_root, "app_web.log")
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)-7s] [%(name)-15s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info("--- Web Application Starting ---")
    logging.info(f"Project Root: {project_root}")
    logging.info(f"Log File: {log_file}")


def shutdown_handler(signum, frame):
    """Handles SIGINT/SIGTERM for graceful shutdown."""
    global keep_running
    if keep_running: # Prevent multiple shutdowns
        logging.warning(f"Received signal {signal.Signals(signum).name}. Initiating graceful shutdown...")
        keep_running = False
    else:
        logging.warning("Shutdown already in progress.")


def initialize_tts():
    """Initializes the TTS model."""
    # ... (function unchanged) ...
    global tts_instance
    logging.info("Initializing TTS model...")
    local_tts_instance = None
    actual_sample_rate = config.DEFAULT_SAMPLE_RATE
    language = config.DEFAULT_LANGUAGE
    try:
        if torch.cuda.is_available(): device = "cuda"
        else: device = "cpu"
        logging.info(f"Using device: {device}")
        torch.serialization.add_safe_globals([XttsConfig, XttsAudioConfig, BaseDatasetConfig, XttsArgs])
        logging.info("(Added safe globals for PyTorch loading)")
        if not os.path.exists(config.TTS_MODEL_PATH):
             logging.critical(f"FATAL: TTS Model directory not found: {config.TTS_MODEL_PATH}")
             return None, actual_sample_rate
        local_tts_instance = TTS( model_path=config.TTS_MODEL_PATH, config_path=os.path.join(config.TTS_MODEL_PATH, 'config.json'), progress_bar=False ).to(device)
        if hasattr(local_tts_instance, 'synthesizer') and hasattr(local_tts_instance.synthesizer, 'output_sample_rate'):
             actual_sample_rate = local_tts_instance.synthesizer.output_sample_rate
             logging.info(f"TTS model loaded. Sample rate: {actual_sample_rate}")
        elif hasattr(local_tts_instance, 'config') and hasattr(local_tts_instance.config, 'audio') and 'sample_rate' in local_tts_instance.config.audio:
             actual_sample_rate = local_tts_instance.config.audio['sample_rate']
             logging.info(f"TTS model loaded. Sample rate (config): {actual_sample_rate}")
        else:
             logging.warning(f"Could not determine TTS sample rate. Using default: {actual_sample_rate}")
        tts_instance = local_tts_instance
        return local_tts_instance, actual_sample_rate
    except Exception as e:
        logging.critical(f"FATAL: Failed to initialize TTS model: {e}", exc_info=True)
        return None, actual_sample_rate


# ------------------------------
# Main Execution Block
# ------------------------------
if __name__ == "__main__":

    setup_logging()

    if not TTS_AVAILABLE:
        logging.critical("Coqui TTS library not found. Cannot start.")
        sys.exit(1)

    logging.info("Starting Narration Application (Web Mode)...")
    logging.info(f"Base Directory (from config): {config.BASE_DIR}")

    # --- Ensure directories exist ---
    try:
        file_system_manager.ensure_directories()
        logging.info(f"Audio Output: {config.AUDIO_OUTPUT_DIR}")
        logging.info(f"Speaker Samples: {config.SPEAKER_SAMPLE_DIR}")
    except Exception as e:
        logging.critical(f"Failed to ensure necessary directories: {e}", exc_info=True)
        print(f"ERROR: Failed to create necessary directories: {e}")
        sys.exit(1)

    # <<< Perform Backend Checks >>>
    logger.info("Checking backend availability...")
    ollama_ok = utils.check_ollama_availability()
    sd_api_ok = utils.check_sd_api_availability(config.SD_API_URL)
    if not ollama_ok:
        logging.warning("Ollama API server does not appear to be running or reachable.")
    if not sd_api_ok:
        logging.warning(f"Stable Diffusion API server does not appear to be running or reachable at {config.SD_API_URL}.")
    # <<< End Backend Checks >>>

    # --- Check Speaker Files ---
    # (Speaker check unchanged)
    logger.info(f"Checking speaker samples in: {config.SPEAKER_SAMPLE_DIR}")
    available_speakers = file_system_manager.find_wav_files(config.SPEAKER_SAMPLE_DIR)
    if not available_speakers:
        logging.warning(f"WARNING: No speaker .wav files found in '{config.SPEAKER_SAMPLE_DIR}'.")
    else:
         logging.info(f"Found {len(available_speakers)} speaker sample(s).")

    # --- Initialize TTS ---
    tts_instance_local, actual_sample_rate = initialize_tts()
    if tts_instance_local is None:
        logging.critical("Exiting due to TTS initialization failure.")
        sys.exit(1)

    # --- Initialize Queue and Worker ---
    generation_queue = queue.Queue()
    logging.info("Starting narrator worker thread...")
    worker_thread = threading.Thread(
        target=narrator_worker, # Imported from app.worker.main
        args=(
            generation_queue, tts_instance_local, actual_sample_rate,
            config.DEFAULT_LANGUAGE,
            None, None, None # No status queue/dict/lock needed for web worker (yet)
        ),
        daemon=False, name="NarratorWorker-Web" # Non-daemon for graceful exit
    )
    worker_thread.start()

    # --- Create and Run Flask App ---
    logging.info("Creating Flask web application using factory...")
    flask_app = create_flask_app(generation_queue)

    # --- Setup Signal Handling ---
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    logging.info("Registered signal handlers for graceful shutdown (SIGINT, SIGTERM).")

    # --- Main Loop for Flask & Shutdown Check ---
    logging.info("Launching Flask web server on http://0.0.0.0:7861...")
    # Start Flask in a separate thread so the main thread can monitor `keep_running`
    flask_thread = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=7861, debug=False, use_reloader=False), daemon=True)
    flask_thread.start()

    try:
        while keep_running and flask_thread.is_alive():
            time.sleep(0.5) # Check for shutdown signal periodically
    except KeyboardInterrupt: # Handle Ctrl+C in main thread too
        logging.warning("KeyboardInterrupt received in main thread.")
        keep_running = False
    except Exception as e:
         logging.critical(f"Main monitoring loop encountered a fatal error: {e}", exc_info=True)
         keep_running = False

    # --- Shutdown Sequence ---
    logging.info("Flask server stop initiated or main loop exited.")
    # Note: Stopping Flask dev server programmatically is tricky. Relies on signal handler.
    # For production (Waitress/Gunicorn), the signal handler is the primary way.

    if worker_thread and worker_thread.is_alive():
        logging.info("Signaling worker thread to exit...")
        generation_queue.put(None) # Send termination signal
        worker_thread.join(timeout=10) # Wait for worker
        if worker_thread.is_alive():
             logging.warning("Worker thread did not exit gracefully after 10 seconds.")
        else:
             logging.info("Worker thread finished.")
    else:
         logging.info("Worker thread already stopped or not started.")

    logging.info("--- Web Application Closed ---")
    logging.shutdown()
    sys.exit(0) # Ensure process exits