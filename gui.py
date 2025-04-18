# gui.py
# Defines the Tkinter AudioPlayerApp class.

import os
import queue
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import simpleaudio as sa
from PIL import Image, ImageTk, ImageOps
import ollama
import json
import traceback
import random
import time
import logging
import threading

# Import from our app structure
from app import config
from app import utils
from app.sd_api import client as sd_client
from app.file_system import manager as file_system_manager # <<< Import file manager

# --- Constants ---
THUMBNAIL_SIZE = (100, 100); GRID_COLS = 4; PLACEHOLDER_COLOR = "#333333"; SELECTED_BORDER_COLOR = "#007ACC"; DEFAULT_BORDER_COLOR = "#404040"; CONTROL_LABEL_WIDTH = 12; PRESET_NAME_PLACEHOLDER = "[Select Preset]"; STYLE_PLACEHOLDER = "[No Style]"
LORA_PLACEHOLDER_TEXT = "<lora:name1:weight1> <lora:name2:weight2>..."

logger = logging.getLogger(__name__)

class AudioPlayerApp:
    def __init__(self, root, available_speaker_paths, generation_queue_ref, status_update_queue, job_statuses, job_status_lock):
        self.root = root
        # Use paths directly from config (ensured by file_system_manager)
        self.audio_output_folder = config.AUDIO_OUTPUT_DIR
        self.image_output_folder = config.IMAGE_OUTPUT_DIR
        self.speaker_folder = config.SPEAKER_SAMPLE_DIR # Keep for browse button

        self.play_obj = None
        self.playback_queue = queue.Queue()
        self.generation_queue = generation_queue_ref
        self.status_update_queue = status_update_queue
        self.available_speaker_paths = available_speaker_paths # Full paths from main_gui
        # Create display names from the full paths passed in
        self.speaker_display_names = sorted([os.path.basename(p) for p in self.available_speaker_paths])

        self.is_playing_queue = False
        self.image_window = None
        self.imgtk = None

        self.thumbnail_widgets = {}
        self.thumbnail_photos = {}
        self.selected_audio_filename = None
        self.selected_thumbnail_frame = None
        self.placeholder_img = self._create_placeholder_image()

        # --- Job Management State / UI ---
        self.job_statuses = job_statuses
        self.job_status_lock = job_status_lock
        self.job_list_update_ms = 1000

        # --- UI Setup ---
        root.title("🎧 Narration Generator & Playlist v2.5 (Refactored FS)")
        root.geometry("750x1100")
        style = ttk.Style()
        style.configure('Card.TFrame', background='#2a2a2b')

        controls_pane = tk.Frame(root, bg="#1e1e1e")
        controls_pane.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 5))
        bottom_pane = tk.Frame(root, bg="#1e1e1e")
        bottom_pane.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)

        # --- Preset Management Frame ---
        preset_frame = ttk.LabelFrame(controls_pane, text="💾 Generation Presets")
        preset_frame.pack(fill='x', pady=(0, 5)); preset_inner_frame = ttk.Frame(preset_frame, padding=5); preset_inner_frame.pack(fill='x')
        ttk.Label(preset_inner_frame, text="Preset:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT)
        self.preset_var = tk.StringVar(); self.preset_dropdown = ttk.Combobox(preset_inner_frame, textvariable=self.preset_var, state="readonly", width=30); self.preset_dropdown.pack(side=tk.LEFT, fill='x', expand=True, padx=(0, 5))
        self.preset_dropdown.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.load_preset_button = ttk.Button(preset_inner_frame, text="📂 Load", command=self._apply_selected_preset, state=tk.DISABLED); self.load_preset_button.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(preset_inner_frame, text="💾 Save", command=self._save_preset).pack(side=tk.LEFT, padx=(0, 5))
        self.delete_preset_button = ttk.Button(preset_inner_frame, text="🗑️ Del", command=self._delete_selected_preset, state=tk.DISABLED); self.delete_preset_button.pack(side=tk.LEFT, padx=(0, 5))


        # --- Generation Controls Frame ---
        gen_controls_frame = ttk.LabelFrame(controls_pane, text="⚙️ Generation Controls"); gen_controls_frame.pack(fill='x')
        left_controls_frame = ttk.Frame(gen_controls_frame, padding=5); left_controls_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5, anchor='n')
        # Mode, Character, Speaker, LLM...
        mode_frame = ttk.Frame(left_controls_frame); mode_frame.pack(fill='x', pady=2); ttk.Label(mode_frame, text="Mode:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.mode_var = tk.StringVar(value="Story"); ttk.Radiobutton(mode_frame, text="Story", variable=self.mode_var, value="Story", command=self.toggle_character_selection).pack(side=tk.LEFT, padx=5); ttk.Radiobutton(mode_frame, text="Conversation", variable=self.mode_var, value="Conversation", command=self.toggle_character_selection).pack(side=tk.LEFT, padx=5)
        self.character_frame = ttk.Frame(left_controls_frame); self.character_frame.pack(fill='x', pady=2); ttk.Label(self.character_frame, text="Character:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.character_var = tk.StringVar(value=config.DEFAULT_CHARACTER); character_options = list(config.CHARACTERS.keys()); self.character_dropdown = ttk.Combobox(self.character_frame, textvariable=self.character_var, values=character_options, state="readonly"); self.character_dropdown.pack(side=tk.LEFT, fill='x', expand=True);
        if config.DEFAULT_CHARACTER in character_options: self.character_dropdown.set(config.DEFAULT_CHARACTER);
        elif character_options: self.character_dropdown.current(0)
        speaker_frame = ttk.Frame(left_controls_frame); speaker_frame.pack(fill='x', pady=2); ttk.Label(speaker_frame, text="Speaker Voice:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.speaker_var = tk.StringVar(); # display names set in init; self.speaker_dropdown = ttk.Combobox(speaker_frame, textvariable=self.speaker_var, values=self.speaker_display_names, state="readonly");
        if self.speaker_display_names: self.speaker_dropdown.current(0)
        self.speaker_dropdown.pack(side=tk.LEFT, fill='x', expand=True); ttk.Button(speaker_frame, text="...", width=2, command=self.browse_speaker_folder).pack(side=tk.LEFT, padx=(2,0))
        ollama_frame = ttk.Frame(left_controls_frame); ollama_frame.pack(fill='x', pady=2); ttk.Label(ollama_frame, text="LLM Model:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.ollama_model_var = tk.StringVar(); self.ollama_model_dropdown = ttk.Combobox(ollama_frame, textvariable=self.ollama_model_var, state="readonly"); self.ollama_model_dropdown.pack(side=tk.LEFT, fill='x', expand=True); ttk.Button(ollama_frame, text="↻", width=2, command=self._fetch_ollama_models).pack(side=tk.LEFT, padx=(2,0));

        # SD Controls...
        right_controls_frame = ttk.Frame(gen_controls_frame, padding=5); right_controls_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        sd_model_frame = ttk.Frame(right_controls_frame); sd_model_frame.pack(fill='x', pady=2); ttk.Label(sd_model_frame, text="SD Checkpoint:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.sd_model_var = tk.StringVar(); self.sd_model_dropdown = ttk.Combobox(sd_model_frame, textvariable=self.sd_model_var, state="readonly"); self.sd_model_dropdown.pack(side=tk.LEFT, fill='x', expand=True); ttk.Button(sd_model_frame, text="↻", width=2, command=self._fetch_sd_models).pack(side=tk.LEFT, padx=(2,0));
        sd_vae_frame = ttk.Frame(right_controls_frame); sd_vae_frame.pack(fill='x', pady=2); ttk.Label(sd_vae_frame, text="SD VAE:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.sd_vae_var = tk.StringVar(); self.sd_vae_entry = ttk.Entry(sd_vae_frame, textvariable=self.sd_vae_var, width=35); self.sd_vae_entry.pack(side=tk.LEFT, fill='x', expand=True);
        if config.SD_DEFAULT_VAE: self.sd_vae_var.set(config.SD_DEFAULT_VAE); self.sd_vae_entry.config(foreground="black");
        else: self.sd_vae_entry.insert(0, "(Backend Default - Leave blank)"); self.sd_vae_entry.config(foreground="grey"); self.sd_vae_entry.bind("<FocusIn>", self._clear_vae_placeholder); self.sd_vae_entry.bind("<FocusOut>", self._restore_vae_placeholder)
        sd_style_frame = ttk.Frame(right_controls_frame); sd_style_frame.pack(fill='x', pady=2); ttk.Label(sd_style_frame, text="SD Style:", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.sd_style_var = tk.StringVar(); self.sd_style_dropdown = ttk.Combobox(sd_style_frame, textvariable=self.sd_style_var, state="readonly"); self.sd_style_dropdown.pack(side=tk.LEFT, fill='x', expand=True); ttk.Button(sd_style_frame, text="↻", width=2, command=self._fetch_sd_styles).pack(side=tk.LEFT, padx=(2,0));
        neg_prompt_frame = ttk.Frame(right_controls_frame); neg_prompt_frame.pack(fill='x', pady=2); ttk.Label(neg_prompt_frame, text="Negative Prompt:", width=CONTROL_LABEL_WIDTH, anchor='nw').pack(side=tk.LEFT); self.sd_neg_prompt_entry = tk.Text(neg_prompt_frame, height=2, width=30, wrap=tk.WORD); self.sd_neg_prompt_entry.pack(side=tk.LEFT, fill='x', expand=True); neg_prompt_scrollbar = ttk.Scrollbar(neg_prompt_frame, orient=tk.VERTICAL, command=self.sd_neg_prompt_entry.yview); neg_prompt_scrollbar.pack(side=tk.RIGHT, fill=tk.Y); self.sd_neg_prompt_entry['yscrollcommand'] = neg_prompt_scrollbar.set; self.sd_neg_prompt_entry.insert('1.0', config.SD_DEFAULT_NEGATIVE_PROMPT)
        # LoRA Syntax...
        lora_frame = ttk.Frame(right_controls_frame); lora_frame.pack(fill='x', pady=2)
        lora_label = ttk.Label(lora_frame, text="LoRA Syntax:", width=CONTROL_LABEL_WIDTH, anchor='nw')
        lora_label.pack(side=tk.LEFT)
        self.lora_syntax_entry = tk.Text(lora_frame, height=2, width=30, wrap=tk.WORD)
        self.lora_syntax_entry.pack(side=tk.LEFT, fill='x', expand=True)
        lora_scrollbar = ttk.Scrollbar(lora_frame, orient=tk.VERTICAL, command=self.lora_syntax_entry.yview)
        lora_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.lora_syntax_entry['yscrollcommand'] = lora_scrollbar.set
        self.lora_syntax_entry.insert('1.0', LORA_PLACEHOLDER_TEXT)
        self.lora_syntax_entry.config(foreground="grey")
        self.lora_syntax_entry.bind("<FocusIn>", self._clear_lora_placeholder)
        self.lora_syntax_entry.bind("<FocusOut>", self._restore_lora_placeholder)

        # Prompt, Count, Generate Button...
        bottom_controls_frame = ttk.Frame(gen_controls_frame); bottom_controls_frame.pack(fill='x', padx=5, pady=5)
        prompt_frame = ttk.Frame(bottom_controls_frame); prompt_frame.pack(fill='x', pady=2); ttk.Label(prompt_frame, text="Prompt/Topic:", width=CONTROL_LABEL_WIDTH, anchor='nw').pack(side=tk.LEFT); self.prompt_entry = tk.Text(prompt_frame, height=3, width=60, wrap=tk.WORD); self.prompt_entry.pack(side=tk.LEFT, fill='x', expand=True); prompt_scrollbar = ttk.Scrollbar(prompt_frame, orient=tk.VERTICAL, command=self.prompt_entry.yview); prompt_scrollbar.pack(side=tk.RIGHT, fill=tk.Y); self.prompt_entry['yscrollcommand'] = prompt_scrollbar.set; ttk.Button(prompt_frame, text="🎲", width=2, command=self.set_random_prompt).pack(side=tk.LEFT, padx=(5,0), anchor='n')
        count_gen_frame = ttk.Frame(bottom_controls_frame); count_gen_frame.pack(fill='x', pady=(5,0)); ttk.Label(count_gen_frame, text="How many?", width=CONTROL_LABEL_WIDTH, anchor='w').pack(side=tk.LEFT); self.count_spinbox = tk.Spinbox(count_gen_frame, from_=1, to=50, width=5); self.count_spinbox.pack(side=tk.LEFT, padx=5); self.count_spinbox.delete(0, "end"); self.count_spinbox.insert(0, 1)
        self.generate_button = ttk.Button(count_gen_frame, text="🚀 Generate Narration", command=self.queue_generation); self.generate_button.pack(side=tk.RIGHT, padx=5)
        self.toggle_character_selection()

        # --- Job Management Frame ---
        job_frame = ttk.LabelFrame(controls_pane, text="📊 Job Queue")
        job_frame.pack(fill='x', pady=(5, 5))
        job_controls_frame = ttk.Frame(job_frame); job_controls_frame.pack(fill='x', pady=(2, 5), padx=5)
        self.cancel_job_button = ttk.Button(job_controls_frame, text="❌ Cancel Sel. Job", command=self._cancel_selected_job, state=tk.DISABLED); self.cancel_job_button.pack(side=tk.LEFT, padx=(0, 5))
        job_list_frame = ttk.Frame(job_frame); job_list_frame.pack(fill='both', expand=True, padx=5, pady=(0, 5))
        cols = ("job_id", "status", "progress", "started"); col_widths = {"job_id": 100, "status": 120, "progress": 100, "started": 150}; col_anchors = {"job_id": 'w', "status": 'w', "progress": 'center', "started": 'center'}
        self.job_tree = ttk.Treeview(job_list_frame, columns=cols, show='headings', height=4)
        for col in cols: self.job_tree.heading(col, text=col.replace('_', ' ').title()); self.job_tree.column(col, width=col_widths.get(col, 100), anchor=col_anchors.get(col, 'center'), stretch=tk.YES)
        job_tree_scroll_y = ttk.Scrollbar(job_list_frame, orient="vertical", command=self.job_tree.yview); job_tree_scroll_x = ttk.Scrollbar(job_list_frame, orient="horizontal", command=self.job_tree.xview); self.job_tree.configure(yscrollcommand=job_tree_scroll_y.set, xscrollcommand=job_tree_scroll_x.set)
        job_tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y); job_tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X); self.job_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.job_tree.bind('<<TreeviewSelect>>', self._on_job_selected)

        # --- Thumbnail Grid Frame ---
        thumbnail_frame_outer = ttk.LabelFrame(bottom_pane, text="🔊 Generated Files"); thumbnail_frame_outer.pack(side=tk.TOP, expand=True, fill='both', padx=0, pady=(5,0))
        grid_controls_frame = ttk.Frame(thumbnail_frame_outer); grid_controls_frame.pack(fill='x', pady=(5, 2))
        ttk.Button(grid_controls_frame, text="🔄 Refresh", command=self.refresh_list).pack(side=tk.LEFT, padx=5)
        self.delete_button = ttk.Button(grid_controls_frame, text="🗑️ Delete Sel.", command=self.delete_selected_file, state=tk.DISABLED); self.delete_button.pack(side=tk.LEFT, padx=5)
        ttk.Button(grid_controls_frame, text="📂 Open Folder", command=self.open_output_folder).pack(side=tk.LEFT, padx=5)
        self.thumbnail_canvas = tk.Canvas(thumbnail_frame_outer, borderwidth=0, background="#1e1e1e");
        self.thumbnail_grid_frame = ttk.Frame(self.thumbnail_canvas, style='Card.TFrame');
        self.thumbnail_scrollbar = ttk.Scrollbar(thumbnail_frame_outer, orient="vertical", command=self.thumbnail_canvas.yview);
        self.thumbnail_canvas.configure(yscrollcommand=self.thumbnail_scrollbar.set)
        self.thumbnail_scrollbar.pack(side="right", fill="y");
        self.thumbnail_canvas.pack(side="left", fill="both", expand=True);
        self.canvas_window = self.thumbnail_canvas.create_window((0, 0), window=self.thumbnail_grid_frame, anchor="nw")
        self.thumbnail_grid_frame.bind("<Configure>", self._on_frame_configure);
        self.thumbnail_canvas.bind("<Configure>", self._on_canvas_configure);
        self.thumbnail_canvas.bind_all("<MouseWheel>", self._on_mousewheel);
        self.thumbnail_canvas.bind_all("<Button-4>", self._on_mousewheel);
        self.thumbnail_canvas.bind_all("<Button-5>", self._on_mousewheel);

        # --- Playback Controls / Status ---
        playback_status_frame = tk.Frame(bottom_pane, bg="#1e1e1e"); playback_status_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5,0))
        self.visualizer_canvas = tk.Canvas(playback_status_frame, height=40, bg='black'); self.visualizer_canvas.pack(fill='x', padx=10, pady=(0, 5));
        playback_frame = ttk.LabelFrame(playback_status_frame, text="▶️ Playback Controls"); playback_frame.pack(fill='x', padx=10, pady=0); pb_buttons = ttk.Frame(playback_frame); pb_buttons.pack(pady=5)
        self.play_sel_button = ttk.Button(pb_buttons, text="▶️ Play Sel.", command=self.play_selected_file, state=tk.DISABLED); self.play_sel_button.pack(side=tk.LEFT, padx=3)
        self.add_queue_button = ttk.Button(pb_buttons, text="➕ Add Queue", command=self.add_selected_to_queue, state=tk.DISABLED); self.add_queue_button.pack(side=tk.LEFT, padx=3)
        ttk.Button(pb_buttons, text="⏯️ Play Queue", command=self.start_queue_playback).pack(side=tk.LEFT, padx=3); ttk.Button(pb_buttons, text="⏹️ Stop", command=self.stop_audio).pack(side=tk.LEFT, padx=3); ttk.Button(pb_buttons, text="⏭️ Skip", command=self.skip_current_in_queue).pack(side=tk.LEFT, padx=3); ttk.Button(pb_buttons, text="❌ Clear Q", command=self.clear_playback_queue).pack(side=tk.LEFT, padx=3)
        status_frame = ttk.Frame(playback_status_frame); status_frame.pack(fill='x', padx=10, pady=(5, 10))
        self.status_label = ttk.Label(status_frame, text="Status: Initializing...", foreground="gray", wraplength=600, justify=tk.LEFT);
        self.status_label.pack(side=tk.LEFT, fill='x', expand=True, padx=(0, 5))
        self.progress = ttk.Progressbar(status_frame, mode='indeterminate', length=100)

        # --- Initial Population & Startups ---
        self._start_waveform_animation()
        self._load_presets()
        self.refresh_list()
        self._start_job_list_refresh()
        self._restore_lora_placeholder(None)

        # Defer Initial Fetches
        self.root.after(50, self._fetch_ollama_models)
        self.root.after(60, self._fetch_sd_models)
        self.root.after(70, self._fetch_sd_styles)


    # --- UI Helpers & Fetch Methods ---
    def _create_placeholder_image(self):
        """Creates a placeholder thumbnail image."""
        try:
            img = Image.new('RGB', THUMBNAIL_SIZE, color=PLACEHOLDER_COLOR)
            img = ImageOps.expand(img, border=2, fill=DEFAULT_BORDER_COLOR)
            return ImageTk.PhotoImage(img)
        except Exception as e: logger.error(f"Error creating placeholder image: {e}", exc_info=True); return None

    def _fetch_ollama_models(self): # Remains the same
        logger.info("Fetching Ollama models..."); model_names = []
        try:
            models_data = ollama.list();
            potential_models = models_data.get('models', [])
            if isinstance(potential_models, list):
                 for m in potential_models:
                     model_id = None
                     try:
                          if isinstance(m, dict): model_id = m.get('model', m.get('name'))
                          elif hasattr(m, 'model'): model_id = m.model
                          elif hasattr(m, 'name'): model_id = m.name
                     except Exception as access_err: logger.warning(f"  Error accessing model data for entry {m}: {access_err}"); continue
                     if model_id and isinstance(model_id, str): model_names.append(model_id)
                     else: logger.warning(f"  Skipping entry - could not extract valid model ID from: {m}")
            else: logger.warning(f"Expected 'models' key to contain a list, but got: {type(potential_models)}")
            model_names = sorted(list(set(model_names)))
            if hasattr(self, 'ollama_model_dropdown') and self.ollama_model_dropdown.winfo_exists():
                if model_names:
                    current_value = self.ollama_model_var.get() if hasattr(self, 'ollama_model_var') else ""
                    self.ollama_model_dropdown['values'] = model_names
                    if not current_value or current_value not in model_names:
                         default_config_model = config.OLLAMA_MODEL_NARRATION
                         if default_config_model in model_names:
                             if hasattr(self, 'ollama_model_var'): self.ollama_model_var.set(default_config_model)
                         elif model_names:
                             if hasattr(self, 'ollama_model_var'): self.ollama_model_var.set(model_names[0])
                    self.ollama_model_dropdown.config(state="readonly")
                    self.set_status(f"Found {len(model_names)} Ollama models.", "gray")
                else:
                    self.ollama_model_dropdown['values'] = [];
                    if hasattr(self, 'ollama_model_var'): self.ollama_model_var.set("[No models found]");
                    self.ollama_model_dropdown.config(state="disabled");
                    self.set_status("No Ollama models found.", "orange")
            else: logger.error("Ollama dropdown widget not found or destroyed during fetch.")
        except Exception as e:
            logger.exception("Error fetching/processing Ollama models:")
            self.set_status(f"Error fetching Ollama models: {e}", "red")
            if hasattr(self, 'ollama_model_dropdown') and self.ollama_model_dropdown.winfo_exists():
                self.ollama_model_dropdown['values'] = [];
                if hasattr(self, 'ollama_model_var'): self.ollama_model_var.set("[Error fetching models]");
                self.ollama_model_dropdown.config(state="disabled")

    def _fetch_sd_models(self): # Uses sd_client
        logger.info("Fetching SD checkpoints via client...");
        sd_model_names = sd_client.fetch_sd_checkpoints()
        try:
            if hasattr(self, 'sd_model_dropdown') and self.sd_model_dropdown.winfo_exists():
                 if sd_model_names:
                      current_value = self.sd_model_var.get() if hasattr(self, 'sd_model_var') else ""
                      self.sd_model_dropdown['values'] = sd_model_names
                      if not current_value or current_value not in sd_model_names:
                           if sd_model_names and hasattr(self, 'sd_model_var'):
                                self.sd_model_var.set(sd_model_names[0])
                      self.sd_model_dropdown.config(state="readonly")
                      self.set_status(f"Found {len(sd_model_names)} SD Checkpoints.", "gray")
                 else:
                      self.sd_model_dropdown['values'] = [];
                      if hasattr(self, 'sd_model_var'): self.sd_model_var.set("[Checkpoints N/A]");
                      self.sd_model_dropdown.config(state="disabled");
                      self.set_status("SD Checkpoints not found or API unreachable.", "orange")
            else: logger.error("SD Model dropdown widget not found or destroyed during fetch.")
        except tk.TclError as e: logger.error(f"TclError updating SD model dropdown: {e}")
        except Exception as e: logger.exception("Unexpected error updating SD model dropdown UI:"); self.set_status(f"Error updating SD model list UI: {e}", "red")

    def _fetch_sd_styles(self): # Uses sd_client
        logger.info("Fetching SD styles via client...");
        style_names = sd_client.fetch_sd_styles()
        try:
            if hasattr(self, 'sd_style_dropdown') and self.sd_style_dropdown.winfo_exists():
                dropdown_values = [STYLE_PLACEHOLDER] + style_names
                self.sd_style_dropdown['values'] = dropdown_values;
                current_value = self.sd_style_var.get() if hasattr(self, 'sd_style_var') else ""
                if not current_value or current_value not in dropdown_values:
                    if hasattr(self, 'sd_style_var'): self.sd_style_var.set(STYLE_PLACEHOLDER)
                new_state = "readonly" if style_names else "disabled"
                self.sd_style_dropdown.config(state=new_state)
                if style_names: self.set_status(f"Found {len(style_names)} SD Styles.", "gray")
                else: self.set_status("SD Styles not found or API unreachable.", "orange")
            else: logger.error("SD Style dropdown widget not found or destroyed during fetch.")
        except tk.TclError as e: logger.error(f"TclError updating SD style dropdown: {e}")
        except Exception as e: logger.exception("Unexpected error updating SD style dropdown UI:"); self.set_status(f"Error updating SD style list UI: {e}", "red")

    # ... (Methods: _start_waveform_animation, toggle_character_selection, set_random_prompt, browse_speaker_folder, open_output_folder) ...
    # These methods do not need changes related to file system manager import
    def _start_waveform_animation(self):
        def animate():
            if not hasattr(self, 'visualizer_canvas') or not self.visualizer_canvas.winfo_exists(): return
            try:
                 width = self.visualizer_canvas.winfo_width(); height = max(1, self.visualizer_canvas.winfo_height()); center_y = height // 2
                 self.visualizer_canvas.delete("wave")
            except tk.TclError: return
            if width <= 1 or height <= 1:
                if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(100, animate)
                return
            num_segments = 60; segment_width = max(1, width // num_segments); spacing = width / num_segments; max_amplitude = max(5, center_y - 2)
            is_playing = False
            try:
                if hasattr(self, 'play_obj') and self.play_obj and self.play_obj.is_playing(): is_playing = True
            except Exception: is_playing = False; self.play_obj = None
            if is_playing:
                for i in range(num_segments): x = int(i * spacing); amplitude = random.randint(int(max_amplitude * 0.2), max_amplitude); y0 = center_y - amplitude; y1 = center_y + amplitude; self.visualizer_canvas.create_line(x, y0, x, y1, fill="lime", width=segment_width, tags="wave")
            else: self.visualizer_canvas.create_line(0, center_y, width, center_y, fill="gray", width=2, tags="wave")
            if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(80, animate)
        if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(50, animate)

    def toggle_character_selection(self):
        try:
            if hasattr(self, 'mode_var') and self.mode_var.get() == "Conversation":
                if hasattr(self, 'character_frame') and self.character_frame.winfo_exists(): self.character_frame.pack(fill='x', pady=2)
            else:
                if hasattr(self, 'character_frame') and self.character_frame.winfo_exists(): self.character_frame.pack_forget()
        except tk.TclError as e: logger.warning(f"TclError toggling character selection visibility: {e}")

    def set_random_prompt(self):
        if hasattr(self, 'prompt_entry') and self.prompt_entry.winfo_exists():
            try:
                random_prompt = utils.generate_random_prompt(); self.prompt_entry.delete('1.0', tk.END); self.prompt_entry.insert('1.0', random_prompt)
            except tk.TclError as e: logger.warning(f"TclError setting random prompt: {e}")

    def browse_speaker_folder(self):
        try:
            speaker_dir = config.SPEAKER_SAMPLE_DIR
            if os.path.isdir(speaker_dir):
                logger.info(f"Opening speaker folder: {speaker_dir}")
                if os.name == 'nt': os.startfile(speaker_dir)
                elif sys.platform == 'darwin': os.system(f'open "{speaker_dir}"')
                else: os.system(f'xdg-open "{speaker_dir}"')
            else:
                logger.warning(f"Speaker directory not found: {speaker_dir}"); messagebox.showwarning("Folder Not Found", f"Speaker directory not found:\n{speaker_dir}", parent=self.root)
        except Exception as e: logger.error(f"Could not open speaker folder: {e}", exc_info=True); messagebox.showerror("Error Opening Folder", f"Could not open folder:\n{e}", parent=self.root)

    def open_output_folder(self):
        try:
            target_dir = config.AUDIO_OUTPUT_DIR
            if not os.path.isdir(target_dir): target_dir = config.OUTPUT_DIR_BASE
            if not os.path.isdir(target_dir): target_dir = config.BASE_DIR
            if os.path.isdir(target_dir):
                 logger.info(f"Opening output folder: {target_dir}")
                 if os.name == 'nt': os.startfile(target_dir)
                 elif sys.platform == 'darwin': os.system(f'open "{target_dir}"')
                 else: os.system(f'xdg-open "{target_dir}"')
            else: logger.warning(f"Could not determine a valid output folder to open."); messagebox.showwarning("Folder Not Found", f"Could not determine a valid output folder to open.", parent=self.root)
        except Exception as e: logger.error(f"Could not open output folder: {e}", exc_info=True); messagebox.showerror("Error Opening Folder", f"Could not open folder:\n{e}", parent=self.root)

    # --- Thumbnail Grid Management ---
    def _on_frame_configure(self, event=None):
        if hasattr(self, 'thumbnail_canvas') and self.thumbnail_canvas.winfo_exists():
            try: self.thumbnail_canvas.configure(scrollregion=self.thumbnail_canvas.bbox("all"))
            except tk.TclError: logger.warning("TclError configuring thumbnail canvas scrollregion.")

    def _on_canvas_configure(self, event=None):
        if hasattr(self, 'thumbnail_canvas') and self.thumbnail_canvas.winfo_exists() and hasattr(self, 'canvas_window'):
             try:
                  canvas_width = event.width; self.thumbnail_canvas.itemconfig(self.canvas_window, width=canvas_width)
             except tk.TclError: logger.warning("TclError configuring thumbnail canvas item width.")

    def _on_mousewheel(self, event):
        if not hasattr(self, 'thumbnail_canvas') or not self.thumbnail_canvas.winfo_exists(): return
        scroll_amount = 0
        try:
            if sys.platform == 'darwin': scroll_amount = -1 * event.delta
            elif event.num == 4: scroll_amount = -1
            elif event.num == 5: scroll_amount = 1
            elif hasattr(event, 'delta'): scroll_amount = -1 if event.delta > 0 else 1
            if scroll_amount != 0: self.thumbnail_canvas.yview_scroll(scroll_amount, "units")
        except tk.TclError: logger.warning("TclError during mousewheel scroll.")
        except Exception as e: logger.error(f"Error handling mousewheel scroll: {e}")

    def refresh_list(self): # <<< Updated to use file_system_manager
        """Reloads and displays thumbnails for generated files."""
        logger.info("Refreshing thumbnail grid...")
        try:
            if not hasattr(self, 'thumbnail_grid_frame') or not self.thumbnail_grid_frame.winfo_exists():
                 logger.warning("Thumbnail grid frame not found or destroyed during refresh.")
                 return

            # Clear Existing Widgets
            for widget in self.thumbnail_grid_frame.winfo_children():
                 try: widget.destroy()
                 except tk.TclError: pass
            self.thumbnail_widgets.clear(); self.thumbnail_photos.clear();
            self.selected_audio_filename = None; self.selected_thumbnail_frame = None;
            self._update_button_states()

            # --- Get File Data using file_system_manager ---
            files_data = file_system_manager.list_generated_files() # <<< USE MANAGER
            # list_generated_files already handles directory not found and sorting

            if not files_data:
                 # Display message if no files or error occurred during listing
                 msg = "No generated files found."
                 # Could potentially check if the directory exists to provide a more specific message
                 if not os.path.isdir(self.audio_output_folder):
                      msg = f"Audio folder not found:\n{self.audio_output_folder}"
                 ttk.Label(self.thumbnail_grid_frame, text=msg, wraplength=300).grid(row=0, column=0, padx=10, pady=10);
            else:
                current_row, current_col = 0, 0
                # Use the data returned by list_generated_files
                for item_data in files_data:
                    audio_filename = item_data['name'] # Already has .wav filename
                    # Construct image path using basename from item_data
                    img_path = os.path.join(config.IMAGE_OUTPUT_DIR, f"{item_data['basename']}.png")
                    try:
                        thumb_frame = tk.Frame(self.thumbnail_grid_frame, relief=tk.SOLID, borderwidth=2, bg=DEFAULT_BORDER_COLOR, padx=1, pady=1)
                        img_label = tk.Label(thumb_frame, borderwidth=0)
                        photo = None
                        try:
                            # Check has_image flag first (optional optimization)
                            if item_data['has_image'] and os.path.exists(img_path):
                                with Image.open(img_path) as img:
                                    img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS);
                                    final_thumb_img = Image.new('RGB', THUMBNAIL_SIZE, PLACEHOLDER_COLOR);
                                    paste_x = (THUMBNAIL_SIZE[0] - img.width) // 2; paste_y = (THUMBNAIL_SIZE[1] - img.height) // 2;
                                    final_thumb_img.paste(img, (paste_x, paste_y));
                                    photo = ImageTk.PhotoImage(final_thumb_img)
                            else:
                                photo = self.placeholder_img
                        except FileNotFoundError: logger.warning(f"Image file not found for thumbnail though has_image=True?: {img_path}"); photo = self.placeholder_img
                        except Exception as e: logger.error(f"Error loading thumbnail for {audio_filename}: {e}", exc_info=False); photo = self.placeholder_img

                        if photo:
                            self.thumbnail_photos[audio_filename] = photo
                            self.thumbnail_widgets[audio_filename] = thumb_frame
                            img_label.config(image=photo)
                            img_label.image = photo # Keep reference!
                        else: img_label.config(text="N/A", bg=PLACEHOLDER_COLOR, width=THUMBNAIL_SIZE[0]//8, height=THUMBNAIL_SIZE[1]//15)

                        img_label.pack(fill="both", expand=True);
                        thumb_frame.grid(row=current_row, column=current_col, padx=5, pady=5, sticky="nsew");

                        callback = lambda e, af=audio_filename: self._on_thumbnail_click(e, af)
                        double_callback = lambda e, af=audio_filename: self._on_thumbnail_double_click(e, af)
                        thumb_frame.bind("<Button-1>", callback); img_label.bind("<Button-1>", callback);
                        thumb_frame.bind("<Double-Button-1>", double_callback); img_label.bind("<Double-Button-1>", double_callback)

                        current_col += 1
                        if current_col >= GRID_COLS: current_col = 0; current_row += 1
                    except tk.TclError as tcl_err: logger.error(f"TclError creating thumbnail widget for {audio_filename}: {tcl_err}. Skipping item."); continue
                    except Exception as general_err: logger.exception(f"Unexpected error creating thumbnail for {audio_filename}: {general_err}"); continue

        except Exception as e:
            self.set_status(f"Error refreshing thumbnails: {e}", "red");
            logger.exception("Error refreshing thumbnail grid:")
        finally:
            if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(50, self._on_frame_configure)
            logger.info("Thumbnail refresh finished.")

    def _on_thumbnail_click(self, event, audio_filename):
        logger.debug(f"Clicked: {audio_filename}");
        newly_selected_frame = self.thumbnail_widgets.get(audio_filename)
        if not newly_selected_frame: logger.warning(f"Thumbnail frame widget not found for {audio_filename} on click."); return
        if self.selected_thumbnail_frame and self.selected_thumbnail_frame != newly_selected_frame:
            try:
                if self.selected_thumbnail_frame.winfo_exists(): self.selected_thumbnail_frame.config(bg=DEFAULT_BORDER_COLOR)
            except tk.TclError: logger.warning("TclError deselecting previous thumbnail frame.")
        try:
            if newly_selected_frame.winfo_exists():
                newly_selected_frame.config(bg=SELECTED_BORDER_COLOR)
                self.selected_audio_filename = audio_filename; self.selected_thumbnail_frame = newly_selected_frame;
            else: self.selected_audio_filename = None; self.selected_thumbnail_frame = None; logger.warning(f"Thumbnail frame for {audio_filename} destroyed before selection.")
        except tk.TclError: logger.warning("TclError selecting new thumbnail frame.")
        self._update_button_states()

    def _on_thumbnail_double_click(self, event, audio_filename):
        logger.debug(f"Double-Clicked: {audio_filename}");
        self._on_thumbnail_click(event, audio_filename);
        if self.selected_audio_filename == audio_filename: self.play_selected_file()

    def delete_selected_file(self): # <<< Updated to use file_system_manager
        """Deletes the selected audio file and its associated text/image."""
        if not self.selected_audio_filename:
             self.set_status("Select an item to delete.", "orange"); return

        base_filename = os.path.splitext(self.selected_audio_filename)[0];
        # --- Call file_system_manager to perform deletion ---
        deleted_count, errors = file_system_manager.delete_generation_files(base_filename)
        # --- End file_system_manager call ---

        if errors:
             self.set_status(f"Deleted {deleted_count} file(s) with errors.", "orange");
             # Join errors for display, limit length if needed
             error_details = "\n".join(errors)
             if len(error_details) > 500: error_details = error_details[:500] + "\n..."
             messagebox.showerror("Deletion Error", error_details, parent=self.root)
        elif deleted_count > 0:
             self.set_status(f"Deleted {deleted_count} file(s) for '{base_filename}'.", "blue")
        else:
             # This case means delete_generation_files found nothing to delete
             self.set_status(f"No files found to delete for '{base_filename}'.", "orange")

        # Clear selection and refresh regardless of outcome
        self.selected_audio_filename = None;
        self.selected_thumbnail_frame = None; # Frame is gone after refresh anyway
        self._update_button_states();
        self.refresh_list()


    # ... (Methods: play_selected_file, add_selected_to_queue, start_queue_playback, play_next_in_queue, stop_audio, skip_current_in_queue, clear_playback_queue, _check_playback_finished) ...
    # These methods do not need changes related to file system manager import
    def play_selected_file(self):
        if not self.selected_audio_filename: self.set_status("Select an item to play.", "orange"); return
        filename = self.selected_audio_filename; filepath = os.path.join(self.audio_output_folder, filename);
        if not os.path.exists(filepath): self.set_status(f"File not found: {filename}", "red"); self.refresh_list(); return
        try:
            self.stop_audio(); wave_obj = sa.WaveObject.from_wave_file(filepath); self.play_obj = wave_obj.play();
            self.set_status(f"Playing: {filename}", "green"); self.show_image_for_file(filename); self._check_playback_finished(filename)
        except sa.libsimpleaudio.SimpleaudioError as sa_err: self.set_status(f"Audio Device Error: {sa_err}", "red"); logger.error(f"SimpleAudio error playing {filepath}: {sa_err}"); self.play_obj = None
        except FileNotFoundError: self.set_status(f"Playback Error: File not found {filename}", "red"); logger.error(f"File not found during playback attempt: {filepath}"); self.play_obj = None; self.refresh_list();
        except Exception as e: self.set_status(f"Playback error: {e}", "red"); logger.exception(f"Error playing {filepath}:"); self.play_obj = None

    def add_selected_to_queue(self):
        if not self.selected_audio_filename: self.set_status("Select an item to add to the queue.", "orange"); return
        filename = self.selected_audio_filename; filepath = os.path.join(self.audio_output_folder, filename);
        if os.path.exists(filepath): self.playback_queue.put(filepath); self.set_status(f"Added to queue: {filename} (Queue size: {self.playback_queue.qsize()})", "blue")
        else: self.set_status(f"File not found, cannot add to queue: {filename}", "red"); self.refresh_list()

    def start_queue_playback(self):
        if self.is_playing_queue: logger.info("Queue playback already in progress."); self.set_status("Queue playback already running.", "orange"); return
        if self.playback_queue.empty(): self.set_status("Playback queue is empty.", "blue"); return
        self.is_playing_queue = True; self.set_status(f"Starting queue playback ({self.playback_queue.qsize()} items)...", "dark cyan"); self.play_next_in_queue()

    def play_next_in_queue(self):
        if not self.is_playing_queue: return
        try:
            if self.play_obj and self.play_obj.is_playing():
                if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(150, self.play_next_in_queue)
                return
        except Exception as e: logger.warning(f"Error checking play_obj status in queue: {e}"); self.play_obj = None
        try:
            filepath = self.playback_queue.get_nowait(); filename = os.path.basename(filepath)
            if not os.path.exists(filepath):
                self.set_status(f"Skipping missing file in queue: {filename}", "orange");
                if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(50, self.play_next_in_queue)
                return
            try:
                wave_obj = sa.WaveObject.from_wave_file(filepath);
                duration_s = 0
                if wave_obj.num_channels > 0 and wave_obj.sample_rate > 0: duration_s = wave_obj.num_frames / wave_obj.sample_rate
                else: logger.warning(f"Wave file {filename} has invalid properties."); duration_s = 2
                self.play_obj = wave_obj.play(); q_size = self.playback_queue.qsize();
                self.set_status(f"Playing Q [{q_size} left]: {filename}", "green"); self.show_image_for_file(filename);
                next_check_delay = int(duration_s * 1000) + 250
                if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(next_check_delay, self.play_next_in_queue)
            except sa.libsimpleaudio.SimpleaudioError as sa_err:
                self.set_status(f"Audio Device Error (Queue): {sa_err}", "red"); logger.error(f"SimpleAudio error playing queued file {filepath}: {sa_err}"); self.play_obj = None;
                if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(500, self.play_next_in_queue)
            except Exception as e:
                self.set_status(f"Playback error (Queue): {e}", "red"); logger.exception(f"Error playing queued file {filepath}:"); self.play_obj = None;
                if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(500, self.play_next_in_queue)
        except queue.Empty:
            self.set_status("Playback queue finished.", "blue"); self.is_playing_queue = False; self.play_obj = None; self._close_image_window()
        except Exception as e: logger.exception("Error handling playback queue:"); self.set_status(f"Queue Error: {e}", "red"); self.is_playing_queue = False; self.play_obj = None

    def stop_audio(self):
        if self.play_obj:
            try:
                 if self.play_obj.is_playing(): logger.debug("Stopping active audio playback."); self.play_obj.stop()
            except Exception as e: logger.warning(f"Error stopping audio object: {e}")
            self.play_obj = None
        if self.is_playing_queue: logger.info("Stopping queue playback."); self.is_playing_queue = False;
        self._close_image_window(); self.set_status("Playback stopped.", "orange")

    def skip_current_in_queue(self):
        if not self.is_playing_queue: self.set_status("Queue is not playing.", "orange"); return
        if self.play_obj and self.play_obj.is_playing():
            logger.info("Skipping current queue item."); self.play_obj.stop(); self.play_obj = None;
            self.set_status("Skipped current queue item.", "blue");
            if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after_idle(self.play_next_in_queue)
        else:
            logger.info("Advancing queue (nothing currently playing)."); self.set_status("Advancing queue...", "blue");
            if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after_idle(self.play_next_in_queue)

    def clear_playback_queue(self):
        self.stop_audio();
        with self.playback_queue.mutex:
            while not self.playback_queue.empty():
                try: self.playback_queue.get_nowait()
                except queue.Empty: break
        logger.info("Playback queue cleared."); self.set_status("Playback queue cleared.", "blue")

    def _check_playback_finished(self, filename_hint=""):
        if self.is_playing_queue: return
        is_playing = False
        try:
             if hasattr(self, 'play_obj') and self.play_obj and self.play_obj.is_playing(): is_playing = True
        except Exception as e: logger.warning(f"Error checking play_obj in _check_playback_finished: {e}"); is_playing = False; self.play_obj = None
        if not is_playing:
             if hasattr(self, 'play_obj') and self.play_obj is None: logger.debug(f"Playback likely finished for: {filename_hint}")
        else:
             if hasattr(self, 'root') and self.root.winfo_exists(): self.root.after(250, lambda: self._check_playback_finished(filename_hint))

    # --- Image Display ---
    def show_image_for_file(self, audio_filename):
        self._close_image_window(); base = os.path.splitext(audio_filename)[0]; image_path = os.path.join(self.image_output_folder, f"{base}.png")
        if os.path.exists(image_path):
            try:
                with Image.open(image_path) as img: max_size = (512, 512); img.thumbnail(max_size, Image.Resampling.LANCZOS); self.imgtk = ImageTk.PhotoImage(img);
                self.image_window = tk.Toplevel(self.root); self.image_window.title(f"🖼️ {os.path.basename(image_path)}"); self.image_window.transient(self.root);
                label = tk.Label(self.image_window, image=self.imgtk); label.pack(); self.image_window.protocol("WM_DELETE_WINDOW", self._on_image_window_close)
                self.image_window.update_idletasks(); main_x = self.root.winfo_x(); main_y = self.root.winfo_y(); main_w = self.root.winfo_width(); main_h = self.root.winfo_height(); popup_w = self.image_window.winfo_width(); popup_h = self.image_window.winfo_height();
                x = main_x + (main_w // 2) - (popup_w // 2); y = main_y + (main_h // 2) - (popup_h // 2); self.image_window.geometry(f"+{max(0, x)}+{max(0, y)}")
            except FileNotFoundError: logger.warning(f"Image file disappeared before display: {image_path}"); self.image_window = None; self.imgtk = None
            except Exception as e: logger.error(f"Error displaying image {image_path}: {e}", exc_info=True); self.set_status(f"Error displaying image: {e}", "orange"); self.image_window = None; self.imgtk = None;
            if self.image_window and self.image_window.winfo_exists():
                    try: self.image_window.destroy()
                    except: pass
        else: logger.info(f"No corresponding image found for {audio_filename} at {image_path}"); self.image_window = None; self.imgtk = None

    def _close_image_window(self):
        if self.image_window:
            try:
                if self.image_window.winfo_exists(): self.image_window.destroy()
            except tk.TclError: logger.warning("TclError destroying image window.")
            except Exception as e: logger.error(f"Unexpected error closing image window: {e}")
            finally: self.image_window = None; self.imgtk = None

    def _on_image_window_close(self): logger.debug("Image window closed by user."); self._close_image_window()

    # --- Placeholder Handling ---
    def _clear_vae_placeholder(self, event=None):
        if hasattr(self, 'sd_vae_var') and hasattr(self, 'sd_vae_entry'):
            try:
                if self.sd_vae_var.get() == "(Backend Default - Leave blank)":
                     if self.sd_vae_entry.winfo_exists(): self.sd_vae_var.set(""); self.sd_vae_entry.config(foreground="black")
            except tk.TclError: pass

    def _restore_vae_placeholder(self, event=None):
         if hasattr(self, 'sd_vae_var') and hasattr(self, 'sd_vae_entry'):
             try:
                 if not self.sd_vae_var.get().strip():
                      if self.sd_vae_entry.winfo_exists(): self.sd_vae_entry.delete(0, tk.END); self.sd_vae_entry.insert(0, "(Backend Default - Leave blank)"); self.sd_vae_entry.config(foreground="grey")
             except tk.TclError: pass

    def _clear_lora_placeholder(self, event=None):
        if hasattr(self, 'lora_syntax_entry') and self.lora_syntax_entry.winfo_exists():
            try:
                current_text = self.lora_syntax_entry.get("1.0", tk.END).strip()
                if current_text == LORA_PLACEHOLDER_TEXT: self.lora_syntax_entry.delete("1.0", tk.END); self.lora_syntax_entry.config(foreground="black")
            except tk.TclError: pass

    def _restore_lora_placeholder(self, event=None):
         if hasattr(self, 'lora_syntax_entry') and self.lora_syntax_entry.winfo_exists():
             try:
                 current_text = self.lora_syntax_entry.get("1.0", tk.END).strip()
                 if not current_text: self.lora_syntax_entry.delete("1.0", tk.END); self.lora_syntax_entry.insert("1.0", LORA_PLACEHOLDER_TEXT); self.lora_syntax_entry.config(foreground="grey")
             except tk.TclError: pass

    # --- Preset Management Logic --- (No changes needed for FS manager)
    def _read_presets_file(self) -> dict:
        try:
            presets_path = config.PRESETS_FILE_PATH
            if os.path.exists(presets_path):
                with open(presets_path, 'r', encoding='utf-8') as f: return json.load(f)
            else: logger.info(f"Presets file not found at {presets_path}, starting fresh."); return {}
        except json.JSONDecodeError as e: logger.error(f"Error decoding presets JSON from {config.PRESETS_FILE_PATH}: {e}", exc_info=True); messagebox.showerror("Preset Error", f"Could not read presets file (invalid JSON):\n{e}", parent=self.root); return {}
        except IOError as e: logger.error(f"Error reading presets file ({config.PRESETS_FILE_PATH}): {e}", exc_info=True); messagebox.showerror("Preset Error", f"Could not read presets file (I/O Error):\n{e}", parent=self.root); return {}
        except Exception as e: logger.exception(f"Unexpected error reading presets file:"); messagebox.showerror("Preset Error", f"Unexpected error reading presets:\n{e}", parent=self.root); return {}

    def _write_presets_file(self, presets_data) -> bool:
        try:
            presets_path = config.PRESETS_FILE_PATH; os.makedirs(os.path.dirname(presets_path), exist_ok=True)
            with open(presets_path, 'w', encoding='utf-8') as f: json.dump(presets_data, f, indent=4)
            logger.info(f"Presets saved to {presets_path}"); return True
        except IOError as e: logger.error(f"Error writing presets file ({config.PRESETS_FILE_PATH}): {e}", exc_info=True); messagebox.showerror("Preset Error", f"Could not write presets file:\n{e}", parent=self.root); return False
        except Exception as e: logger.exception(f"Unexpected error writing presets file:"); messagebox.showerror("Preset Error", f"Unexpected error writing presets:\n{e}", parent=self.root); return False

    def _load_presets(self):
        logger.info("Loading presets..."); presets_data = self._read_presets_file(); preset_names = sorted(list(presets_data.keys())); dropdown_values = [PRESET_NAME_PLACEHOLDER] + preset_names
        try:
            if hasattr(self, 'preset_dropdown') and self.preset_dropdown.winfo_exists(): self.preset_dropdown['values'] = dropdown_values;
            if hasattr(self, 'preset_var'): self.preset_var.set(PRESET_NAME_PLACEHOLDER)
        except tk.TclError as e: logger.error(f"TclError loading presets into dropdown: {e}")
        self._update_button_states()

    def _save_preset(self):
        preset_name = simpledialog.askstring("Save Preset", "Enter a name for this preset:", parent=self.root)
        if not preset_name or not preset_name.strip():
             self.set_status("Preset save cancelled.", "orange"); return
        preset_name = preset_name.strip();

        # <<< Outer try block STARTS here >>>
        try:
            # Safely get values from UI elements
            selected_style = self.sd_style_var.get() if hasattr(self, 'sd_style_var') else ""

            lora_text = ""
            # Nested try-except for individual widget access is okay, but optional if outer block catches TclError
            if hasattr(self, 'lora_syntax_entry') and self.lora_syntax_entry.winfo_exists():
                 try:
                      lora_text_raw = self.lora_syntax_entry.get("1.0", tk.END).strip()
                      if lora_text_raw != LORA_PLACEHOLDER_TEXT: lora_text = lora_text_raw
                 except tk.TclError: pass # Ignore if this specific widget destroyed

            sd_neg_prompt = ""
            if hasattr(self, 'sd_neg_prompt_entry') and self.sd_neg_prompt_entry.winfo_exists():
                try: sd_neg_prompt = self.sd_neg_prompt_entry.get("1.0", tk.END).strip()
                except tk.TclError: pass # Ignore if this specific widget destroyed

            mode = self.mode_var.get() if hasattr(self, 'mode_var') else "Story"
            character = self.character_var.get() if hasattr(self, 'character_var') else config.DEFAULT_CHARACTER
            speaker = self.speaker_var.get() if hasattr(self, 'speaker_var') else ""
            ollama_model = self.ollama_model_var.get() if hasattr(self, 'ollama_model_var') else ""
            sd_checkpoint = self.sd_model_var.get() if hasattr(self, 'sd_model_var') else ""
            sd_vae_raw = self.sd_vae_var.get() if hasattr(self, 'sd_vae_var') else ""

            # Process VAE value (can stay inside the try block)
            sd_vae = sd_vae_raw.replace("(Backend Default - Leave blank)", "").strip() or None

            # --- Now create the settings dictionary ---
            current_settings = {
                "mode": mode,
                "character": character,
                "speaker": speaker,
                "ollama_model": ollama_model if not ollama_model.startswith("[") else "",
                "sd_checkpoint": sd_checkpoint if not sd_checkpoint.startswith("[") else "",
                "sd_vae": sd_vae,
                "sd_style": selected_style if selected_style != STYLE_PLACEHOLDER else "",
                "sd_negative_prompt": sd_neg_prompt,
                "lora_syntax": lora_text
            }

            # --- Continue with saving logic ---
            presets_data = self._read_presets_file()
            if preset_name in presets_data:
                if not messagebox.askyesno("Overwrite Preset", f"Preset '{preset_name}' already exists. Overwrite?", parent=self.root):
                    self.set_status("Preset save cancelled (overwrite).", "orange"); return

            presets_data[preset_name] = current_settings
            if self._write_presets_file(presets_data):
                self.set_status(f"Preset '{preset_name}' saved.", "blue");
                self._load_presets();
                if hasattr(self, 'preset_var'):
                     try: self.preset_var.set(preset_name)
                     except tk.TclError: pass
                self._update_button_states()
            else:
                self.set_status(f"Failed to save preset '{preset_name}'. Check logs.", "red")

        # <<< Outer except block ADDED here, indented to match the first 'try:' >>>
        except tk.TclError as e:
            # Catch errors if UI elements are accessed after being destroyed
            logger.error(f"TclError accessing UI elements during preset save: {e}")
            self.set_status("Error accessing UI element during save.", "red")
            messagebox.showerror("Preset Save Error", f"Failed to read settings from UI:\n{e}", parent=self.root)
        except Exception as e:
             # Catch any other unexpected errors during value retrieval or processing
             logger.exception("Unexpected error gathering settings for preset save:")
             self.set_status(f"Error saving preset: {e}", "red")
             messagebox.showerror("Preset Save Error", f"Unexpected error preparing preset data:\n{e}", parent=self.root)
    def _apply_selected_preset(self):
        preset_name = self.preset_var.get() if hasattr(self, 'preset_var') else PRESET_NAME_PLACEHOLDER
        if not preset_name or preset_name == PRESET_NAME_PLACEHOLDER: self.set_status("No preset selected to load.", "orange"); return
        presets_data = self._read_presets_file()
        if preset_name not in presets_data: self.set_status(f"Preset '{preset_name}' not found in file. Refreshing list.", "red"); self._load_presets(); return
        settings = presets_data[preset_name]; logger.info(f"Applying preset: {preset_name}"); logger.debug(f"Preset settings: {settings}")
        try:
            if hasattr(self, 'mode_var'): self.mode_var.set(settings.get("mode", "Story")); self.toggle_character_selection()
            if hasattr(self, 'mode_var') and self.mode_var.get() == "Conversation":
                char_name = settings.get("character", config.DEFAULT_CHARACTER);
                if hasattr(self, 'character_dropdown') and self.character_dropdown.winfo_exists():
                    valid_chars = self.character_dropdown['values']
                    if char_name in valid_chars: self.character_var.set(char_name);
                    elif valid_chars: self.character_var.set(valid_chars[0]);
                    else: self.character_var.set("")
            speaker_name = settings.get("speaker", "");
            if hasattr(self, 'speaker_var'):
                if speaker_name in self.speaker_display_names: self.speaker_var.set(speaker_name);
                elif self.speaker_display_names: self.speaker_var.set(self.speaker_display_names[0]);
                else: self.speaker_var.set("")
            ollama_model = settings.get("ollama_model", "");
            if hasattr(self, 'ollama_model_dropdown') and self.ollama_model_dropdown.winfo_exists():
                 valid_models = list(self.ollama_model_dropdown['values'])
                 if ollama_model and ollama_model in valid_models: self.ollama_model_var.set(ollama_model);
                 elif valid_models and ollama_model: logger.warning(f"Preset Ollama model '{ollama_model}' not found. Using first."); self.ollama_model_var.set(valid_models[0]);
                 elif valid_models: self.ollama_model_var.set(valid_models[0]);
                 else: self.ollama_model_var.set("")
            sd_model = settings.get("sd_checkpoint", "");
            if hasattr(self, 'sd_model_dropdown') and self.sd_model_dropdown.winfo_exists():
                valid_models = list(self.sd_model_dropdown['values'])
                if sd_model and sd_model in valid_models: self.sd_model_var.set(sd_model);
                elif valid_models and sd_model: logger.warning(f"Preset SD Checkpoint '{sd_model}' not found. Using first."); self.sd_model_var.set(valid_models[0]);
                elif valid_models: self.sd_model_var.set(valid_models[0]);
                else: self.sd_model_var.set("")
            vae_value = settings.get("sd_vae", "");
            if hasattr(self, 'sd_vae_var'): self.sd_vae_var.set(vae_value if vae_value else ""); self._restore_vae_placeholder(None)
            neg_prompt = settings.get("sd_negative_prompt", config.SD_DEFAULT_NEGATIVE_PROMPT);
            if hasattr(self, 'sd_neg_prompt_entry') and self.sd_neg_prompt_entry.winfo_exists():
                try: self.sd_neg_prompt_entry.delete("1.0", tk.END); self.sd_neg_prompt_entry.insert("1.0", neg_prompt)
                except tk.TclError: pass
            style_name = settings.get("sd_style", "");
            if hasattr(self, 'sd_style_dropdown') and self.sd_style_dropdown.winfo_exists():
                 valid_styles = list(self.sd_style_dropdown['values'])
                 if style_name and style_name in valid_styles: self.sd_style_var.set(style_name);
                 else: self.sd_style_var.set(STYLE_PLACEHOLDER)
            lora_text = settings.get("lora_syntax", "")
            if hasattr(self, 'lora_syntax_entry') and self.lora_syntax_entry.winfo_exists():
                try:
                    self.lora_syntax_entry.delete("1.0", tk.END)
                    if lora_text: self.lora_syntax_entry.insert("1.0", lora_text); self.lora_syntax_entry.config(foreground="black")
                    else: self._restore_lora_placeholder(None)
                except tk.TclError: pass
            self.set_status(f"Preset '{preset_name}' loaded.", "blue")
        except tk.TclError as e: logger.error(f"TclError applying preset settings for '{preset_name}': {e}"); messagebox.showerror("Preset Load Error", f"Error accessing UI element while applying preset:\n{e}", parent=self.root); self.set_status(f"Error applying preset '{preset_name}'.", "red")
        except Exception as e: logger.exception(f"Error applying preset {preset_name}:"); messagebox.showerror("Preset Load Error", f"Error applying preset settings:\n{e}", parent=self.root); self.set_status(f"Error loading preset '{preset_name}'.", "red")

    def _delete_selected_preset(self):
        preset_name = self.preset_var.get() if hasattr(self, 'preset_var') else PRESET_NAME_PLACEHOLDER
        if not preset_name or preset_name == PRESET_NAME_PLACEHOLDER: self.set_status("No preset selected to delete.", "orange"); return
        if not messagebox.askyesno("Confirm Deletion", f"Are you sure you want to delete the preset '{preset_name}'?", parent=self.root): self.set_status("Preset deletion cancelled.", "orange"); return
        presets_data = self._read_presets_file()
        if preset_name in presets_data:
            del presets_data[preset_name]
            if self._write_presets_file(presets_data): self.set_status(f"Preset '{preset_name}' deleted.", "blue"); self._load_presets()
            else: self.set_status(f"Failed to delete preset '{preset_name}'.", "red")
        else: self.set_status(f"Preset '{preset_name}' not found, could not delete.", "red"); self._load_presets()

    def _on_preset_selected(self, event=None): self._update_button_states()

    # --- Job Management Logic --- (No changes needed for FS manager)
    def _format_time(self, timestamp):
        if not timestamp: return ""
        try: return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
        except (ValueError, TypeError, OSError): return str(timestamp)

    def _refresh_job_list(self):
        if not hasattr(self, 'job_tree') or not self.job_tree.winfo_exists(): return
        try:
            selected_iids = self.job_tree.selection(); current_items_in_tree = set(self.job_tree.get_children()); jobs_to_display = {}
            with self.job_status_lock: jobs_to_display = self.job_statuses.copy()
            sorted_job_ids = sorted(jobs_to_display.keys(), key=lambda jid: jobs_to_display[jid].get('started_at', 0), reverse=True)
            processed_iids = set()
            for job_id in sorted_job_ids:
                processed_iids.add(job_id); job_data = jobs_to_display.get(job_id, {}); status = job_data.get('status', 'unknown'); items_done = job_data.get('items_done', 0); items_total = job_data.get('items_total', 1); started_at = job_data.get('started_at'); started_str = self._format_time(started_at); progress_str = "-"
                if status == 'running' and items_total > 0: progress_str = f"{items_done}/{items_total}"
                elif status == 'completed': progress_str = "Done"
                elif status == 'completed_warnings': progress_str = "Done (Warn)"
                elif status == 'failed': progress_str = "Failed"
                elif status == 'cancelled': progress_str = "Cancelled"
                elif status == 'cancelling': progress_str = "Cancelling..."
                elif status == 'queued': progress_str = "Queued"
                values = (job_id, status.replace('_', ' ').title(), progress_str, started_str)
                if job_id in current_items_in_tree: self.job_tree.item(job_id, values=values)
                else: self.job_tree.insert("", 0, iid=job_id, values=values)
            iids_to_remove = current_items_in_tree - processed_iids
            for iid_to_remove in iids_to_remove:
               if self.job_tree.exists(iid_to_remove): self.job_tree.delete(iid_to_remove)
            valid_selection = [iid for iid in selected_iids if self.job_tree.exists(iid)]
            if valid_selection: self.job_tree.selection_set(valid_selection)
            else: self._on_job_selected()
        except tk.TclError as e: logger.error(f"TclError refreshing job list: {e}")
        except Exception as e: logger.exception("Error refreshing job list:")
        finally:
            if hasattr(self, 'root') and self.root.winfo_exists():
                try: self.root.after(self.job_list_update_ms, self._refresh_job_list)
                except tk.TclError: logger.info("GUI root destroyed, stopping job list refresh loop.")
                except Exception as e: logger.error(f"Error rescheduling job list refresh: {e}")

    def _start_job_list_refresh(self):
        if hasattr(self, 'root') and self.root.winfo_exists():
            logger.info("Starting periodic job list refresh.")
            try: self.root.after(self.job_list_update_ms, self._refresh_job_list)
            except Exception as e: logger.error(f"Failed to schedule initial job list refresh: {e}")
        else: logger.warning("Cannot start job list refresh: root window not available.")

    def _on_job_selected(self, event=None):
        selected_items = []; can_cancel = False
        if hasattr(self, 'job_tree') and self.job_tree.winfo_exists():
             try: selected_items = self.job_tree.selection()
             except tk.TclError: selected_items = []
        if selected_items:
            job_id = selected_items[0];
            with self.job_status_lock: job_data = self.job_statuses.get(job_id, {}); status = job_data.get('status')
            if status in ['running', 'queued']: can_cancel = True
        new_state = tk.NORMAL if can_cancel else tk.DISABLED
        if hasattr(self, 'cancel_job_button') and self.cancel_job_button.winfo_exists():
            try: self.cancel_job_button.config(state=new_state)
            except tk.TclError: pass

    def _cancel_selected_job(self):
        if not hasattr(self, 'job_tree') or not self.job_tree.winfo_exists(): return
        try: selected_items = self.job_tree.selection()
        except tk.TclError: selected_items = []; logger.warning("TclError getting job selection for cancel.")
        if not selected_items: self.set_status("No job selected to cancel.", "orange"); return
        job_id = selected_items[0]; cancelled_signal_sent = False
        with self.job_status_lock:
            job_data = self.job_statuses.get(job_id)
            if job_data:
                 status = job_data.get('status')
                 if status in ['running', 'queued']: job_data['cancelled'] = True; job_data['status'] = 'cancelling'; cancelled_signal_sent = True; logger.info(f"Sent cancel signal for job {job_id}")
                 else: logger.warning(f"Cannot cancel job {job_id} with status '{status}'")
            else: logger.warning(f"Job {job_id} not found in status dictionary for cancellation.")
        if cancelled_signal_sent: self.set_status(f"Cancellation requested for {job_id}.", "blue"); self._on_job_selected()
        else: self.set_status(f"Cannot cancel job {job_id} (invalid state or not found).", "orange")


    # --- Generation --- (No changes needed for FS manager)
    def queue_generation(self):
        try:
            count_str = "1"
            if hasattr(self, 'count_spinbox') and self.count_spinbox.winfo_exists():
                 try: count_str = self.count_spinbox.get()
                 except tk.TclError: pass
            prompt = ""
            if hasattr(self, 'prompt_entry') and self.prompt_entry.winfo_exists():
                 try: prompt = self.prompt_entry.get("1.0", tk.END).strip()
                 except tk.TclError: pass
            selected_speaker_name = self.speaker_var.get() if hasattr(self, 'speaker_var') else ""
            selected_ollama_model = self.ollama_model_var.get() if hasattr(self, 'ollama_model_var') else ""
            selected_sd_model = self.sd_model_var.get() if hasattr(self, 'sd_model_var') else ""
            selected_sd_vae_raw = self.sd_vae_var.get() if hasattr(self, 'sd_vae_var') else ""
            negative_prompt = ""
            if hasattr(self, 'sd_neg_prompt_entry') and self.sd_neg_prompt_entry.winfo_exists():
                 try: negative_prompt = self.sd_neg_prompt_entry.get("1.0", tk.END).strip()
                 except tk.TclError: pass
            selected_style = self.sd_style_var.get() if hasattr(self, 'sd_style_var') else ""
            lora_syntax = None
            if hasattr(self, 'lora_syntax_entry') and self.lora_syntax_entry.winfo_exists():
                try:
                    lora_syntax_raw = self.lora_syntax_entry.get("1.0", tk.END).strip()
                    if lora_syntax_raw and lora_syntax_raw != LORA_PLACEHOLDER_TEXT: lora_syntax = lora_syntax_raw
                except tk.TclError: pass
            try: count = int(count_str); count = max(1, min(count, 50))
            except ValueError: count = 1; logger.warning("Invalid count input, defaulting to 1.")
            selected_sd_vae = None
            if selected_sd_vae_raw and selected_sd_vae_raw != "(Backend Default - Leave blank)": selected_sd_vae = selected_sd_vae_raw.strip()
            selected_sd_model_final = selected_sd_model if selected_sd_model and not selected_sd_model.startswith("[") else None
            selected_ollama_model_final = selected_ollama_model if selected_ollama_model and not selected_ollama_model.startswith("[") else None
            style_list = [selected_style] if selected_style and selected_style != STYLE_PLACEHOLDER else None
            if not negative_prompt: negative_prompt = config.SD_DEFAULT_NEGATIVE_PROMPT
            if not selected_speaker_name: messagebox.showerror("Input Error", "Please select a speaker voice.", parent=self.root); return
            if not selected_ollama_model_final: messagebox.showerror("Input Error", "Please select a valid Ollama model.", parent=self.root); return
            selected_voice_path = None
            for p in self.available_speaker_paths: # Use full paths stored in init
                 if os.path.basename(p) == selected_speaker_name: selected_voice_path = p; break
            if not selected_voice_path or not os.path.exists(selected_voice_path): logger.error(f"Selected speaker file not found: Name='{selected_speaker_name}', Expected Path='{selected_voice_path}'"); messagebox.showerror("File Error", f"Speaker file not found:\n{selected_speaker_name}", parent=self.root); return
            mode = self.mode_var.get() if hasattr(self, 'mode_var') else "Story"
            character = self.character_var.get() if mode == "Conversation" and hasattr(self, 'character_var') else None
            job = { "count": count, "custom_prompt": prompt, "speaker_wav": selected_voice_path, "mode": mode, "character": character, "ollama_model": selected_ollama_model_final, "sd_checkpoint": selected_sd_model_final, "sd_vae": selected_sd_vae, "sd_negative_prompt": negative_prompt, "sd_styles": style_list, "lora_syntax": lora_syntax }
            self.generation_queue.put(job); logger.info(f"Queued generation job: {count} item(s).")
            style_info = f" / Style: {selected_style}" if style_list else ""; lora_info = " / LoRA" if lora_syntax else ""; sd_info = f"SD: {selected_sd_model_final or 'Default'}"
            self.set_status(f"Queued {count} task(s) using {selected_ollama_model_final} / {sd_info}{style_info}{lora_info}.", "blue")
        except tk.TclError as e: logger.error(f"TclError accessing UI elements during job queueing: {e}"); messagebox.showerror("Queue Error", f"Error accessing UI element:\n{e}", parent=self.root)
        except ValueError: messagebox.showerror("Input Error", "Invalid count specified. Please enter a number.", parent=self.root)
        except Exception as e: logger.exception("Error queuing generation job:"); messagebox.showerror("Queue Error", f"Error queuing job:\n{e}", parent=self.root)


    # --- Status and Progress Update Methods --- (No changes needed for FS manager)
    def set_status(self, msg, color="gray"):
        if hasattr(self, 'status_label') and self.status_label.winfo_exists():
            try: self.status_label.config(text=f"Status: {msg}", foreground=color)
            except tk.TclError: logger.warning("TclError setting status label text/color.")
            except Exception as e: logger.error(f"Error setting status label: {e}")
        else: logger.warning(f"Status label widget missing when trying to set status: {msg}")

    def start_progress(self):
        if hasattr(self, 'progress') and self.progress.winfo_exists():
            try:
                if not self.progress.winfo_manager(): self.progress.pack(side=tk.RIGHT, padx=5, pady=(0,2), anchor='e')
                self.progress.start()
            except tk.TclError: logger.warning("TclError starting progress bar.")
            except Exception as e: logger.error(f"Error starting progress bar: {e}")
        else: logger.warning("Progress bar widget missing when trying to start.")

    def stop_progress(self, msg="Idle", color="gray"):
        if hasattr(self, 'progress') and self.progress.winfo_exists():
             try:
                 self.progress.stop()
                 if self.progress.winfo_manager(): self.progress.pack_forget()
             except tk.TclError: logger.warning("TclError stopping/hiding progress bar.")
             except Exception as e: logger.error(f"Error stopping progress bar: {e}")
        else: logger.warning("Progress bar widget missing when trying to stop.")
        self.set_status(msg, color)

    # --- Status Queue Checker --- (No changes needed for FS manager)
    def _check_status_updates(self):
        try:
            while not self.status_update_queue.empty():
                try:
                    message = self.status_update_queue.get_nowait()
                    command = message[0]
                    if command == "status": _, msg, color = message; self.set_status(msg, color)
                    elif command == "start_progress": self.start_progress()
                    elif command == "stop_progress": _, msg, color = message; self.stop_progress(msg, color)
                    elif command == "refresh_list": self.refresh_list()
                    else: logger.warning(f"Unknown command received on status queue: {command}")
                    self.status_update_queue.task_done()
                except queue.Empty: break
                except tk.TclError as e: logger.error(f"TclError processing status update '{message}': {e}")
                except Exception as e: logger.exception(f"Error processing status update: {message}")
        finally:
            if hasattr(self, 'root') and self.root.winfo_exists():
                try: self.root.after(100, self._check_status_updates)
                except tk.TclError: logger.info("GUI root destroyed, stopping status checker.")
                except Exception as e: logger.error(f"Error rescheduling status checker: {e}")

    # --- Button State Logic --- (No changes needed for FS manager)
    def _update_button_states(self):
        thumbnail_selected = bool(self.selected_audio_filename); thumbnail_state = tk.NORMAL if thumbnail_selected else tk.DISABLED
        try:
            if hasattr(self, 'delete_button') and self.delete_button.winfo_exists(): self.delete_button.config(state=thumbnail_state)
            if hasattr(self, 'play_sel_button') and self.play_sel_button.winfo_exists(): self.play_sel_button.config(state=thumbnail_state)
            if hasattr(self, 'add_queue_button') and self.add_queue_button.winfo_exists(): self.add_queue_button.config(state=thumbnail_state)
        except tk.TclError: logger.warning("TclError updating thumbnail button states.")
        except Exception as e: logger.error(f"Error updating thumbnail button states: {e}")
        preset_selected = False
        if hasattr(self, 'preset_var'):
             try: preset_selected = self.preset_var.get() != PRESET_NAME_PLACEHOLDER;
             except tk.TclError: pass
        preset_state = tk.NORMAL if preset_selected else tk.DISABLED
        try:
            if hasattr(self, 'load_preset_button') and self.load_preset_button.winfo_exists(): self.load_preset_button.config(state=preset_state)
            if hasattr(self, 'delete_preset_button') and self.delete_preset_button.winfo_exists(): self.delete_preset_button.config(state=preset_state)
        except tk.TclError: logger.warning("TclError updating preset button states.")
        except Exception as e: logger.error(f"Error updating preset button states: {e}")
        self._on_job_selected()