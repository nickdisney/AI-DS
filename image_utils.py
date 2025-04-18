# image_utils.py
# Utilities specifically for image generation

import base64
import requests
import os
import config # To get default SD settings and URL
import traceback # Added for better error details
import json # Import json for override_settings
import logging # Added logging

logger = logging.getLogger(__name__)

# Updated function signature to include styles and lora_syntax_string
def generate_image_sd(
    prompt,
    output_path,
    negative_prompt=config.SD_DEFAULT_NEGATIVE_PROMPT,
    checkpoint_name=None,
    vae_name=config.SD_DEFAULT_VAE,
    styles=None, # Expects a list of strings
    lora_syntax_string: str | None = None, # <-- Add LoRA syntax parameter
    # Placeholder for future additions:
    # hires_settings=None
):
    """
    Generates an image using the Stable Diffusion API specified in config,
    including negative prompts, checkpoint override, VAE override, styles, and LoRA syntax.

    Args:
        prompt (str): The positive text prompt.
        output_path (str): Full path to save the generated image.
        negative_prompt (str, optional): The negative prompt. Defaults to config.SD_DEFAULT_NEGATIVE_PROMPT.
        checkpoint_name (str, optional): Filename of the SD checkpoint. Defaults to None.
        vae_name (str, optional): Filename of the VAE. Defaults to config.SD_DEFAULT_VAE.
        styles (list[str], optional): List of style names to apply. Defaults to None.
        lora_syntax_string (str, optional): String containing LoRA syntax (e.g., "<lora:name:weight>"). Defaults to None.

    Returns:
        bool: True if image generation and saving were successful, False otherwise.
    """
    if not prompt:
        logger.warning("Image generation skipped: No prompt provided.")
        return False

    # --- Build Override Settings ---
    override_settings = {}
    if checkpoint_name:
        override_settings["sd_model_checkpoint"] = checkpoint_name
        logger.info(f"   Overriding Checkpoint: {checkpoint_name}")
    if vae_name:
        override_settings["sd_vae"] = vae_name
        logger.info(f"   Overriding VAE: {vae_name}")

    # --- Prepare Final Prompt with LoRA ---
    final_prompt = prompt
    if lora_syntax_string and lora_syntax_string.strip():
        clean_lora_syntax = lora_syntax_string.strip()
        final_prompt = f"{prompt}, {clean_lora_syntax}" # Append LoRA syntax
        logger.info(f"   Applying LoRA Syntax: {clean_lora_syntax}")
    # ---------------------------------------

    # --- Build the main payload ---
    payload = {
        "prompt": final_prompt, # Use the potentially modified prompt
        "negative_prompt": negative_prompt,
        "steps": config.SD_DEFAULT_STEPS,
        "sampler_index": config.SD_DEFAULT_SAMPLER,
        "width": config.SD_DEFAULT_WIDTH,
        "height": config.SD_DEFAULT_HEIGHT,
        "batch_size": 1,
        "cfg_scale": config.SD_DEFAULT_CFG_SCALE,
        "styles": styles if styles else [], # Send empty list or list of names
        "override_settings": override_settings if override_settings else None,
        "override_settings_restore_afterwards": True
    }

    payload = {k: v for k, v in payload.items() if v is not None} # Clean None override_settings

    # --- Log Request Details ---
    logger.info(f"   🎨 Sending image generation request to {config.SD_API_URL}...")
    logger.info(f"      Final Prompt: {final_prompt[:100]}...") # Log final prompt
    if negative_prompt: logger.info(f"      Negative: {negative_prompt[:100]}...")
    if styles: logger.info(f"      Styles: {styles}")
    # LoRA syntax logged earlier if used
    # Checkpoint/VAE logged earlier if used

    # --- Make API Call ---
    try:
        response = requests.post(config.SD_API_URL, json=payload, timeout=180)
        response.raise_for_status()
        r = response.json()

        # --- Process Response ---
        if 'images' in r and r['images']:
            image_data_base64 = r['images'][0]
            if "," in image_data_base64:
                image_data_base64 = image_data_base64.split(",", 1)[-1]

            try:
                image_bytes = base64.b64decode(image_data_base64)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(image_bytes)
                logger.info(f"   ✅ Image saved successfully: {output_path}")
                return True
            except base64.binascii.Error as decode_err:
                logger.error(f"   ❌ Error decoding base64 image data: {decode_err}")
                return False
            except IOError as io_err:
                logger.error(f"   ❌ Error saving image file: {io_err}")
                return False
            except Exception as inner_e:
                logger.exception(f"   ❌ Unexpected error during image save/decode: {inner_e}")
                return False
        else:
            logger.warning(f"   ⚠️ No image data returned in the API response. Response: {r}")
            if 'info' in r:
                logger.warning(f"      API Info/Error: {r['info']}")
            return False

    except requests.exceptions.Timeout:
        logger.error(f"   ❌ Image generation request timed out ({180}s).")
        return False
    except requests.exceptions.ConnectionError as conn_err:
         logger.error(f"   ❌ Image generation connection error: {conn_err}")
         logger.error(f"      Is the Stable Diffusion backend running at {config.SD_API_URL}?")
         return False
    except requests.exceptions.RequestException as req_err:
        logger.error(f"   ❌ Image generation request failed: {req_err}")
        try: logger.error(f"      Response Body: {response.text}")
        except: pass
        return False
    except Exception as e:
        logger.exception(f"   ❌ An unexpected error occurred during image generation API call: {e}")
        return False