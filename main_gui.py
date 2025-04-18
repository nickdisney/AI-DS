# main_gui.py
# Entry point script to initialize and launch the Tkinter GUI application.

import os
import sys
import time
import torch
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox # Keep messagebox
import traceback
import logging

# Add project root to sys.path FIRST
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
print(f"DEBUG: Added project root to sys.path: {project_root}") # Debug print

# Import from our app structure
from app import config
# <<< Import new check functions >>>
from app import utils
from app.file_system import manager as file_system_manager
from app.worker.main import narrator_worker
from app.gui.main_window import AudioPlayerApp # Import from correct location

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

# ------------------------------
# Setup and Launch
# ------------------------------
if __name__ == "__main__":

    # --- Configure Logging EARLY---
    log_file = os.path.join(project_root, "app_gui.log")
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)-7s] [%(name)-15s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger(__name__)
    logger.info("--- GUI Application Starting ---")
    logger.info(f"Project Root: {project_root}")
    logger.info(f"Log File: {log_file}")

    if not TTS_AVAILABLE:
        logger.critical("Coqui TTS library not found. Cannot start.")
        # Show error box before exiting
        root = tk.Tk()
        root.withdraw() # Hide the root window
        messagebox.showerror("Startup Error", "Coqui TTS library not found.\nPlease install it using: pip install TTS")
        root.destroy()
        sys.exit(1)

    logger.info("Starting Narration Application (GUI Mode)...")
    logger.info(f"Base Directory (from config): {config.BASE_DIR}")

    # --- Ensure directories exist ---
    try:
        file_system_manager.ensure_directories()
        logger.info(f"Audio Output: {config.AUDIO_OUTPUT_DIR}")
        logger.info(f"Speaker Samples: {config.SPEAKER_SAMPLE_DIR}")
    except Exception as e:
        logger.critical(f"Failed to ensure necessary directories: {e}", exc_info=True)
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("Startup Error", f"Failed to create necessary directories:\n{e}")
        root.destroy(); sys.exit(1)

    # --- Check Backend Availability ---
    logger.info("Checking backend availability...")
    ollama_ok = utils.check_ollama_availability()
    sd_api_ok = utils.check_sd_api_availability(config.SD_API_URL)
    backend_warnings = []
    if not ollama_ok:
        backend_warnings.append("Ollama API server does not appear to be running or reachable. LLM features will fail.")
    if not sd_api_ok:
        backend_warnings.append(f"Stable Diffusion API server does not appear to be running or reachable at the configured URL ({config.SD_API_URL}). Image features will fail.")

    # --- Find Speaker Files ---
    # (Speaker check unchanged)
    logger.info(f"Looking for speaker samples in: {config.SPEAKER_SAMPLE_DIR}")
    available_speakers = file_system_manager.find_wav_files(config.SPEAKER_SAMPLE_DIR)
    if not available_speakers:
        logger.critical(f"No speaker .wav files found in '{config.SPEAKER_SAMPLE_DIR}'.")
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("Startup Error", f"No speaker .wav files found in:\n{config.SPEAKER_SAMPLE_DIR}\n\nPlease add at least one speaker voice sample.")
        root.destroy(); sys.exit(1)
    logger.info(f"Found {len(available_speakers)} speaker sample(s).")
    for speaker_path in available_speakers[:5]: logger.info(f"  - {os.path.basename(speaker_path)}")
    if len(available_speakers) > 5: logger.info(f"  ... and {len(available_speakers) - 5} more.")


    # --- Initialize TTS ---
    logger.info("Initializing TTS model...")
    tts_instance = None
    actual_sample_rate = config.DEFAULT_SAMPLE_RATE
    language = config.DEFAULT_LANGUAGE
    try:
        # ... (TTS initialization logic unchanged) ...
        if torch.cuda.is_available(): device = "cuda"
        else: device = "cpu"
        logger.info(f"Using device: {device}")
        torch.serialization.add_safe_globals([XttsConfig, XttsAudioConfig, BaseDatasetConfig, XttsArgs])
        logger.info("(Added safe globals for PyTorch loading)")
        if not os.path.exists(config.TTS_MODEL_PATH):
             logger.critical(f"TTS Model directory not found at: {config.TTS_MODEL_PATH}");
             root = tk.Tk(); root.withdraw()
             messagebox.showerror("Startup Error", f"TTS Model directory not found:\n{config.TTS_MODEL_PATH}")
             root.destroy(); sys.exit(1)

        tts_instance = TTS( model_path=config.TTS_MODEL_PATH, config_path=os.path.join(config.TTS_MODEL_PATH, 'config.json'), progress_bar=False ).to(device)

        if hasattr(tts_instance, 'synthesizer') and hasattr(tts_instance.synthesizer, 'output_sample_rate'):
             actual_sample_rate = tts_instance.synthesizer.output_sample_rate
             logger.info(f"TTS model loaded. Sample rate: {actual_sample_rate}")
        elif hasattr(tts_instance, 'config') and hasattr(tts_instance.config, 'audio') and 'sample_rate' in tts_instance.config.audio:
             actual_sample_rate = tts_instance.config.audio['sample_rate']
             logger.info(f"TTS model loaded. Sample rate (from config): {actual_sample_rate}")
        else:
             logger.warning(f"Could not determine TTS sample rate. Using default: {actual_sample_rate}")

    except Exception as e:
        logger.critical(f"Failed to initialize TTS model: {e}", exc_info=True);
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("TTS Initialization Error", f"Failed to initialize TTS model:\n{e}")
        root.destroy(); sys.exit(1)

    # --- Initialize Queues & Shared State ---
    generation_queue = queue.Queue()
    status_update_queue = queue.Queue()
    job_statuses = {}
    job_status_lock = threading.Lock()

    # --- Create Tkinter Root Window ---
    root = tk.Tk()

    # --- Create the GUI App Instance ---
    logger.info("Initializing GUI...")
    try:
        app = AudioPlayerApp(
            root, available_speakers, generation_queue,
            status_update_queue, job_statuses, job_status_lock
        )
    except Exception as e:
        logger.critical(f"Failed to initialize AudioPlayerApp GUI: {e}", exc_info=True)
        messagebox.showerror("GUI Initialization Error", f"Failed to create main application window:\n{e}")
        root.destroy(); sys.exit(1)

    # <<< Show backend warnings AFTER GUI is initialized >>>
    if backend_warnings:
        warning_message = "Backend Availability Issues:\n\n" + "\n\n".join(backend_warnings)
        logger.warning("Showing backend availability warnings to user.")
        messagebox.showwarning("Backend Check", warning_message, parent=root)
        # Optionally disable generate button if backends are critical
        # if not ollama_ok or not sd_api_ok:
        #     if hasattr(app, 'generate_button'): app.generate_button.config(state=tk.DISABLED)
        #     app.set_status("Warning: Critical backend(s) unavailable.", "red")

    # --- Start the Worker Thread ---
    logger.info("Starting narrator worker thread...")
    worker_thread = threading.Thread(
        target=narrator_worker, # Imported from app.worker.main
        args=(
            generation_queue, tts_instance, actual_sample_rate, language,
            status_update_queue, job_statuses, job_status_lock
            ),
        daemon=False, name="NarratorWorker" # Changed from True to False - important for clean exit
    )
    worker_thread.start()

    # --- Setup Window Closing ---
    logger.info("Configuring window closing protocol...")
    def on_closing():
        logger.info("Shutdown requested via window close button...")
        if messagebox.askokcancel("Quit", "Do you want to quit? This will stop ongoing generation.", parent=root):
            logger.info("Initiating graceful shutdown...")
            if hasattr(app, 'generate_button') and app.generate_button.winfo_exists():
                try: app.generate_button.config(state=tk.DISABLED)
                except tk.TclError: pass

            status_msg = "Shutting down... waiting for worker..."
            if hasattr(app, 'set_status'): app.set_status(status_msg, "orange")
            else: logger.info(status_msg)

            logger.info("Signaling worker thread to exit...")
            generation_queue.put(None) # Send termination signal
            worker_thread.join(timeout=10) # Wait for worker
            if worker_thread.is_alive(): logger.warning("Worker thread did not exit gracefully after 10 seconds.")
            else: logger.info("Worker thread finished.")
            logger.info("Destroying GUI window.")
            try: root.destroy()
            except tk.TclError: pass
        else:
            logger.info("Shutdown cancelled by user.")

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # --- Start Tkinter Main Event Loop ---
    logger.info("Launching GUI main loop...")
    try:
        if hasattr(app, '_check_status_updates'):
            root.after(100, app._check_status_updates) # Start status checker
        root.mainloop()
    except Exception as e:
        logger.exception("Unhandled error in GUI main loop:")
        # Attempt graceful shutdown even on error
        on_closing() # Call the same shutdown logic
    finally:
        logger.info("--- GUI Application Closed ---")
        logging.shutdown()
        # Ensure exit even if threads hang, though worker is now non-daemon
        # os._exit(0) # Force exit if needed, but non-daemon thread + join should be preferred