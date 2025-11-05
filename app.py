import os
import time
import base64
import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify
from werkzeug.utils import secure_filename

# --- Configuration ---
# NOTE: The ImgBB API key MUST be set as an environment variable.
# You can get one for free at https://api.imgbb.com/
IMG_BB_API_KEY = os.getenv("IMGBB_API_KEY")

# RunPod API Endpoints
RUNPOD_ENDPOINT = "https://api.runpod.ai/v2/qwen-image-edit"
RUNPOD_RUN_URL = f"{RUNPOD_ENDPOINT}/run"
RUNPOD_STATUS_URL = f"{RUNPOD_ENDPOINT}/status"

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_secure_secret_key_for_session_management' # Required for production
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max 16MB file size

# --- Helper Functions ---

def upload_to_imgbb(image_file):
    """
    Uploads an image file to ImgBB and returns the hosted URL.
    """
    if not IMG_BB_API_KEY:
        raise EnvironmentError("IMGBB_API_KEY environment variable is not set.")

    app.logger.info("Uploading image to ImgBB...")

    try:
        # ImgBB prefers the image as a base64 encoded string in the 'image' field
        img_bytes = image_file.read()
        base64_image = base64.b64encode(img_bytes)

        # ImgBB API call
        response = requests.post(
            f"https://api.imgbb.com/1/upload?key={IMG_BB_API_KEY}",
            data={
                "image": base64_image,
                "name": secure_filename(image_file.filename)
            },
            timeout=30 # Set a timeout for the upload
        )

        response.raise_for_status()
        data = response.json()

        if data.get('success'):
            hosted_url = data['data']['url']
            app.logger.info(f"ImgBB upload successful. URL: {hosted_url}")
            return hosted_url
        else:
            app.logger.error(f"ImgBB upload failed: {data.get('error', 'Unknown Error')}")
            raise Exception(f"ImgBB Upload Failed: {data.get('error', 'Unknown Error')}")

    except requests.exceptions.RequestException as e:
        app.logger.error(f"HTTP Error during ImgBB upload: {e}")
        raise Exception(f"Network error with ImgBB: {e}")
    except Exception as e:
        app.logger.error(f"General Error during ImgBB upload: {e}")
        raise

def run_qwen_image_edit(runpod_key, img_url, prompt, negative_prompt, seed):
    """
    Submits the image URL and prompt to the RunPod Qwen Image Edit API and polls for the result.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {runpod_key}"
    }

    # Prepare the input payload for the RunPod API
    payload = {
        "input": {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": int(seed) if seed else -1, # Default seed to -1 if empty
            "image": img_url,
            "output_format": "png",
            "enable_safety_checker": True
        }
    }

    app.logger.info("Sending initial request to RunPod API...")
    # 1. Initial RunPod request to get the job ID
    try:
        run_response = requests.post(RUNPOD_RUN_URL, headers=headers, json=payload, timeout=60)
        run_response.raise_for_status()
        job_id = run_response.json().get('id')
    except requests.exceptions.RequestException as e:
        app.logger.error(f"HTTP Error during initial RunPod request: {e}")
        raise Exception(f"RunPod API Error (Initial Request): {e}")

    if not job_id:
        raise Exception(f"RunPod API did not return a job ID. Response: {run_response.text}")

    app.logger.info(f"RunPod job started, ID: {job_id}. Starting to poll...")
    
    # 2. Polling for job status
    status = "IN_QUEUE"
    max_polls = 100
    polls = 0
    
    while status in ["IN_QUEUE", "IN_PROGRESS"] and polls < max_polls:
        polls += 1
        time.sleep(3) # Wait 3 seconds between polls
        
        try:
            status_response = requests.get(f"{RUNPOD_STATUS_URL}/{job_id}", headers=headers, timeout=10)
            status_response.raise_for_status()
            status_data = status_response.json()
            status = status_data.get('status')
            
            app.logger.info(f"Job {job_id} status: {status}")

            if status == "COMPLETED":
                # FIX: Check for the 'result' key, which contains the final image URL based on your error output.
                output = status_data.get('output')
                final_image_url = output.get('result') if output and isinstance(output, dict) else None

                if final_image_url:
                    return final_image_url
                else:
                    error_message = f"RunPod job COMPLETED but missing 'result' (final image URL) in output. Full output: {output}"
                    app.logger.error(error_message)
                    raise Exception(f"RunPod job output error: {error_message}")
            
            if status in ["FAILED", "CANCELED"]:
                error_message = status_data.get('error', f'Job failed with status: {status}')
                app.logger.error(f"RunPod job failed: {error_message}")
                raise Exception(f"RunPod job failed: {error_message}")

        except requests.exceptions.RequestException as e:
            app.logger.error(f"HTTP Error during RunPod polling: {e}")
            # Continue polling unless it's a critical error
        except Exception as e:
            app.logger.error(f"General Error during RunPod polling: {e}")
            raise

    if polls >= max_polls:
        raise Exception("RunPod job timed out (maximum polling attempts reached).")
    
    return None # Should not happen if loop logic is correct

# --- Routes ---

@app.route('/', methods=['GET'])
def index():
    """Renders the main form."""
    if not IMG_BB_API_KEY:
        return render_template('index.html', error_message="ERROR: IMGBB_API_KEY environment variable is not set on the server.", original_url=None, edited_url=None)

    return render_template('index.html', error_message=None, original_url=None, edited_url=None)

@app.route('/process', methods=['POST'])
def process():
    """Handles the form submission and API pipeline."""
    runpod_key = request.form.get('runpod_key')
    prompt = request.form.get('prompt')
    negative_prompt = request.form.get('negative_prompt', "")
    seed = request.form.get('seed')
    image_file = request.files.get('image')
    
    if not all([runpod_key, prompt, image_file]):
        return render_template('index.html', error_message="Please fill in all required fields (Key, Prompt, and Image).", original_url=None, edited_url=None)
    
    if not image_file.filename:
         return render_template('index.html', error_message="Please select an image file to upload.", original_url=None, edited_url=None)

    original_url = None
    edited_url = None
    error_message = None

    try:
        # Step 1: Upload to ImgBB
        original_url = upload_to_imgbb(image_file)
        
        # Step 2: Call RunPod API and poll for result
        edited_url = run_qwen_image_edit(runpod_key, original_url, prompt, negative_prompt, seed)

    except Exception as e:
        error_message = str(e)
        app.logger.error(f"Pipeline Failed: {error_message}")

    return render_template('index.html', 
                           error_message=error_message, 
                           original_url=original_url, 
                           edited_url=edited_url,
                           # Pass back form data to re-populate the form on error
                           form_data={'runpod_key': runpod_key, 'prompt': prompt, 'negative_prompt': negative_prompt, 'seed': seed})

if __name__ == '__main__':
    # For local development: ensure you set IMGBB_API_KEY in your environment
    # e.g., export IMGBB_API_KEY='your_key'
    app.run(debug=True)