import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = BASE_DIR / "projects"
DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"

# Ensure directories exist
for directory in [PROJECTS_DIR, DATABASE_DIR, LOGS_DIR, MODELS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Database Config
DATABASE_PATH = DATABASE_DIR / "project.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

# Logs Config
LOG_FILE_PATH = LOGS_DIR / "application.log"

# AI Model Config
DEFAULT_MODEL = "qwen2.5:7b-instruct"
SUPPORTED_MODELS = {
    "qwen2.5:7b-instruct": "Qwen 2.5 7B Instruct (Default)",
    "gemma:7b": "Gemma 7B",
    "mistral:7b": "Mistral 7B",
    "llama3.1:8b": "Llama 3.1 8B"
}
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434")

# TTS Model Config (kokoro-onnx v1.0 release assets)
KOKORO_ONNX_FILENAME = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_FILENAME = "voices-v1.0.bin"

# Kokoro model download URLs (from thewh1teagle kokoro-onnx releases)
KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

DEFAULT_VOICE = "af_bella"
SUPPORTED_VOICES = [
    # Female Voices
    "af_bella", "af_sarah", "af_nicole", "af_sky",
    # Male Voices
    "am_adam", "am_michael", "am_fenrir", "am_puck"
]

DEFAULT_SPEECH_SPEED = 1.0
DEFAULT_VOLUME = 1.0
AUDIO_SAMPLE_RATE = 24000  # Kokoro works at 24kHz

# API Server Settings
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
