# generate_images_batch.py
# Standalone script to generate images for existing text files
# containing image prompts.

import os
import argparse
import sys
import traceback
import logging # Use logging for better output control

# --- Setup Project Root Path ---
# Assumes this script is in the project root directory.
# If it's moved elsewhere, adjust the path logic.
project_root = os.path.dirname(os.path.abspath(__file__))
# Add the project root to sys.path so we can import the 'app' package
sys.path.insert(0, project_root)
print(f"DEBUG: Added project root to sys.path: {project_root}")

# --- Configure Logging for Batch Script ---
# Keep simple console logging for this standalone script
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-7s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)] # Log directly to console
)
logger = logging.getLogger("batch_image_gen")

# --- Import from App Structure ---
try:
    from app import config
    from app import utils
    from app.sd_api import client as sd_client # Import the new SD client
    # File system manager might be useful for ensure_directories if needed, but os.makedirs is simple enough here
    # from app.file_system import manager as file_system_manager
except ImportError as e:
    logger.critical(f"Error importing project modules: {e}")
    logger.critical("Please ensure this script is run from the project root directory")
    logger.critical("and the 'app' directory exists with necessary files (__init__.py, config.py, etc.).")
    sys.exit(1)
except Exception as e:
    logger.critical(f"An unexpected error occurred during module import: {e}")
    traceback.print_exc()
    sys.exit(1)


def main(text_folder, image_folder, sd_url_override=None):
    """
    Processes text files in text_folder, extracts image prompts,
    and generates images in image_folder using the SD API client.
    """
    logger.info("--- Batch Image Generation Script ---")
    logger.info(f"Text Input Folder: {text_folder}")
    logger.info(f"Image Output Folder: {image_folder}")

    # --- Handle SD API URL Override ---
    # The sd_client uses the URL from app.config by default.
    # If an override is provided, we need to temporarily change app.config.SD_API_URL
    # before calling the client. This is a bit crude but works for a standalone script.
    # A cleaner way might involve passing the URL directly to the sd_client functions
    # if they were modified to accept it as an optional argument.
    original_sd_url = config.SD_API_URL # Store original
    using_url = original_sd_url

    if sd_url_override:
        logger.info(f"Using SD API URL (Override): {sd_url_override}")
        config.SD_API_URL = sd_url_override # Temporarily override config value
        using_url = sd_url_override
    else:
        logger.info(f"Using SD API URL (Config): {config.SD_API_URL}")

    # --- Validate Input Folder ---
    if not os.path.isdir(text_folder):
        logger.error(f"❌ Error: Text input folder not found: {text_folder}")
        # Restore original URL before exiting if it was overridden
        if sd_url_override: config.SD_API_URL = original_sd_url
        sys.exit(1)

    # --- Ensure Output Folder Exists ---
    try:
        # Use os.makedirs directly, simple enough for this script
        os.makedirs(image_folder, exist_ok=True)
        logger.info(f"Ensured image output folder exists: {image_folder}")
    except OSError as e:
        logger.error(f"❌ Error creating output folder {image_folder}: {e}")
        if sd_url_override: config.SD_API_URL = original_sd_url # Restore URL
        sys.exit(1)

    processed_files = 0
    skipped_existing = 0
    skipped_no_prompt = 0
    generated_count = 0
    errors = 0

    # Use the extraction function from app.utils
    extract_func = utils.extract_story_and_image_prompt

    # --- Iterate through text files ---
    try:
        all_files = os.listdir(text_folder)
    except OSError as e:
        logger.error(f"❌ Error reading text input directory {text_folder}: {e}")
        if sd_url_override: config.SD_API_URL = original_sd_url # Restore URL
        sys.exit(1)

    logger.info(f"Found {len(all_files)} total entries in {text_folder}. Processing .txt files...")

    for filename in all_files:
        if not filename.lower().endswith(".txt"):
            continue

        processed_files += 1
        text_filepath = os.path.join(text_folder, filename)
        base_name = os.path.splitext(filename)[0]
        # Use the configured image output directory
        image_filepath = os.path.join(image_folder, f"{base_name}.png")

        logger.info(f"\nProcessing ({processed_files}/{len(all_files)}): {filename}")

        # --- Check if image already exists ---
        if os.path.exists(image_filepath):
            logger.info(f"   ➡️ Skipping (Image already exists: {os.path.basename(image_filepath)})")
            skipped_existing += 1
            continue

        # --- Read text file and extract prompt ---
        try:
            with open(text_filepath, "r", encoding="utf-8") as f:
                contents = f.read()

            # Extract using the unified function from app.utils
            _story, image_prompt = extract_func(contents) # We only need the prompt here

            if not image_prompt:
                logger.warning(f"   ⚠️ Skipping (No image prompt found in file)")
                skipped_no_prompt += 1
                continue

            logger.info(f"   💬 Found Prompt: {image_prompt[:80]}...")

            # --- Generate Image using sd_client ---
            # Note: This uses defaults from config for steps, sampler, neg prompt etc.
            # We are only passing the essential prompt and output path.
            # If batch script needs more control (e.g., different neg prompts),
            # add more arguments to this script and pass them to sd_client.generate_image.
            logger.info("   ⏳ Requesting image generation...")
            success = sd_client.generate_image(
                prompt=image_prompt,
                output_path=image_filepath
                # Add other args like negative_prompt if needed
                # negative_prompt=config.SD_DEFAULT_NEGATIVE_PROMPT, # Example
            )
            if success:
                # generate_image already logs success/failure path
                # logger.info(f"   ✅ Image generated: {os.path.basename(image_filepath)}") # Redundant logging
                generated_count += 1
            else:
                 # generate_image already logs the error details
                 logger.error(f"   ❌ Failed to generate image for {filename}")
                 errors += 1

        except FileNotFoundError:
             logger.error(f"   ❌ Error: Could not read file {text_filepath} (might have been deleted?)")
             errors += 1
        except Exception as e:
            logger.error(f"   ❌ An unexpected error occurred processing {filename}: {e}")
            logger.error(traceback.format_exc()) # Log full traceback for unexpected errors
            errors += 1

    # --- Restore original config URL if it was overridden ---
    if sd_url_override:
        logger.info("Restoring original SD API URL in config.")
        config.SD_API_URL = original_sd_url

    # --- Print Summary ---
    logger.info("\n--- Batch Generation Summary ---")
    logger.info(f"Files Processed:      {processed_files}")
    logger.info(f"Images Generated:     {generated_count}")
    logger.info(f"Skipped (Exists):     {skipped_existing}")
    logger.info(f"Skipped (No Prompt): {skipped_no_prompt}")
    logger.info(f"Errors:               {errors}")
    logger.info("-----------------------------")


if __name__ == "__main__":
    # Default paths from app.config
    default_text_dir = config.TEXT_OUTPUT_DIR
    default_image_dir = config.IMAGE_OUTPUT_DIR
    default_sd_url = config.SD_API_URL

    parser = argparse.ArgumentParser(
        description="Batch generate images from text files containing prompts, using the application's config and modules.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
        )
    parser.add_argument(
        "-t", "--text-folder",
        default=default_text_dir,
        help="Folder containing the .txt files with story and image prompts."
    )
    parser.add_argument(
        "-i", "--image-folder",
        default=default_image_dir,
        help="Folder where generated .png images will be saved."
    )
    parser.add_argument(
        "-u", "--sd-url",
        default=None, # Default is None, meaning use the one from config
        help=f"Override the Stable Diffusion API URL (Default from config: {default_sd_url})"
    )

    args = parser.parse_args()

    # Run the main function
    main(args.text_folder, args.image_folder, args.sd_url)