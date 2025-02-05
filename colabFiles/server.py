from flask import Flask, request, jsonify, make_response, abort, current_app, send_file
from flask_cors import CORS, cross_origin
from PIL import Image
from threading import Thread, Lock
import torch
from torchvision.transforms import functional as TF
import numpy as np
from startup.utils import *
from startup.ml_utils import *
from startup.load_models import loadModels
from startup.create_sample_function import createSampleFunction
from startup.generate_samples import generateSamples
import io
import base64
from datetime import datetime

def startServer(device, model_params, model, diffusion, ldm_model, bert_model, clip_model, clip_preprocess, normalize):
    """
    Starts a Flask server to handle inpainting requests from a remote UI.

    Note that this server can only handle a single client. In the future, using a direct connection would probably
    be superior, but it's not worth the extra effort right now.
    """


    print("Starting server...")
    app = Flask(__name__)
    CORS(app)
    context = app.app_context()
    context.push()

    with context:
        current_app.lastRequest = None
        current_app.lastError = None
        current_app.in_progress = False
        current_app.thread = None
        current_app.samples = {}
        current_app.lock = Lock()

    # Check if the server's up:
    @app.route("/", methods=["GET"])
    @cross_origin()
    def health_check():
        return jsonify(success=True)

    # Start an inpainting request:
    @app.route("/", methods=["POST"])
    @cross_origin()
    def startInpainting():
        # Extract arguments from body, convert images from base64
        json = request.get_json(force=True)
        def requestedOrDefault(key, defaultValue):
            if key in json:
                return json[key]
            return defaultValue
        batch_size = requestedOrDefault('batch_size', 1)
        num_batches = requestedOrDefault('num_batches', 1)
        width = requestedOrDefault('width', 256)
        height = requestedOrDefault('height', 256)

        edit = None
        mask = None
        try:
            edit = loadImageFromBase64(json["edit"])
        except Exception as err:
            print(f"loading edit image failed, {err}")
            abort(make_response({"error": f"loading edit image failed, {err}"}, 400))
        try:
            mask = loadImageFromBase64(json["mask"])
        except Exception as err:
            print(f"loading mask image failed, {err}")
            abort(make_response({"error": f"loading mask image failed, {err}"}, 400))

        sample_fn = None
        try:
            sample_fn, clip_score_fn = createSampleFunction(
                    device,
                    model,
                    model_params,
                    bert_model,
                    clip_model,
                    clip_preprocess,
                    ldm_model,
                    diffusion,
                    normalize,
                    edit=edit,
                    mask=mask,
                    prompt = requestedOrDefault("prompt", ""),
                    negative = requestedOrDefault("negative", ""),
                    guidance_scale = requestedOrDefault("guidanceScale", 5.0),
                    batch_size = batch_size,
                    width = width,
                    height = height,
                    cutn = requestedOrDefault("cutn", 16),
                    skip_timesteps = requestedOrDefault("skipSteps", False))
        except Exception as err:
            abort(make_response({"error": f"creating sample function failed, {err}"}, 500))

        def save_sample(i, sample, clip_score=False):
            with current_app.lock:
                timestamp = datetime.timestamp(datetime.now())
                try:
                    def addImageToResponse(k, image):
                        name = f'{i * batch_size + k:05}'
                        current_app.samples[name] = { "image": imageToBase64(image), "timestamp": timestamp }
                    foreachImageInSample(sample, batch_size, ldm_model, addImageToResponse)
                except Exception as err:
                    current_app.lastError = f"sample save error: {err}"
                    print(current_app.lastError)

        def run_thread():
            with context:
                generateSamples(device,
                        ldm_model,
                        diffusion,
                        sample_fn,
                        save_sample,
                        batch_size,
                        num_batches,
                        width,
                        height)
                with current_app.lock:
                    current_app.in_progress = False
                
        # Start image generation thread:
        with current_app.lock:
            if current_app.in_progress or current_app.thread and current_app.thread.is_alive():
                abort(make_response({error: "Cannot start a new operation, an existing operation is still running"}, 409))
            current_app.samples = {}
            current_app.in_progress = True
            current_app.thread = Thread(target = run_thread)
            current_app.thread.start()

        return jsonify(success=True)

    # Request updated images:
    @app.route("/sample", methods=["GET"])
    @cross_origin()
    def list_updated():
        json = request.get_json(force=True)
        # Parse (sampleName, timestamp) pairs from request.samples
        # Check (sampleName, timestamp) pairs from the most recent request. If any missing from the request or have a
        # newer timestamp, set response.samples[sampleName] = { timestamp, base64Image }
        response = { "samples": {} }
        with current_app.lock:
            for key in current_app.samples:
                if key not in json["samples"] or json["samples"][key] < current_app.samples[key]["timestamp"]:
                    response["samples"][key] = current_app.samples[key]
            # If any errors were saved for the most recent request, use those to set response.errors
            if current_app.lastError != "":
                response["error"] = current_app.lastError

            # Check if the most recent request is finished, use this to set response.in_progress.
            response["in_progress"] = current_app.in_progress
        return response

    return app
