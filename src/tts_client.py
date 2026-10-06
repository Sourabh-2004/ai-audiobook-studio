import logging
import re
import asyncio
from pathlib import Path
import httpx
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro
from src.config import (
    MODELS_DIR, KOKORO_MODEL_URL, KOKORO_VOICES_URL,
    KOKORO_ONNX_FILENAME, KOKORO_VOICES_FILENAME,
    DEFAULT_VOICE, AUDIO_SAMPLE_RATE
)

logger = logging.getLogger(__name__)

class TTSClient:
    def __init__(self):
        self.model_path = MODELS_DIR / KOKORO_ONNX_FILENAME
        self.voices_path = MODELS_DIR / KOKORO_VOICES_FILENAME
        self.kokoro = None

    def is_model_downloaded(self) -> bool:
        """Returns True if the ONNX model and voices file are present and non-empty."""
        min_sizes = {
            KOKORO_ONNX_FILENAME: 50_000_000,
            KOKORO_VOICES_FILENAME: 1_000_000,
        }
        for filename, min_size in min_sizes.items():
            path = MODELS_DIR / filename
            if not path.exists() or path.stat().st_size < min_size:
                return False
        return True

    async def download_model_files(self, progress_callback=None):
        """
        Downloads the Kokoro ONNX model and voices file from Hugging Face.
        Accepts an optional callback to report progress.
        """
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        
        # Predefined sizes for reliable progress tracking
        est_sizes = {
            KOKORO_ONNX_FILENAME: 92400000,
            KOKORO_VOICES_FILENAME: 28200000
        }
        total_est_bytes = sum(est_sizes.values())
        
        overall_downloaded = 0
        for fname in est_sizes:
            if (MODELS_DIR / fname).exists():
                overall_downloaded += est_sizes[fname]

        headers = {"User-Agent": "novel-condenser/1.0"}
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True, headers=headers) as client:
            for url, filename in [(KOKORO_MODEL_URL, KOKORO_ONNX_FILENAME), (KOKORO_VOICES_URL, KOKORO_VOICES_FILENAME)]:
                dest_path = MODELS_DIR / filename
                min_size = 1_000_000 if filename.endswith(".bin") else 50_000_000
                if dest_path.exists() and dest_path.stat().st_size >= min_size:
                    logger.info(f"{filename} already exists, skipping download.")
                    continue
                if dest_path.exists():
                    logger.warning(f"{filename} is incomplete; re-downloading.")
                    dest_path.unlink()
                
                logger.info(f"Downloading {filename} from GitHub releases...")
                if progress_callback:
                    progress_callback(f"Starting download: {filename}...", (overall_downloaded / total_est_bytes) * 100.0)

                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"Failed to download {filename}. HTTP {response.status_code}. "
                            f"Try downloading manually from {url}"
                        )
                        
                    total_bytes = int(response.headers.get("content-length", 0))
                    if total_bytes <= 0:
                        total_bytes = est_sizes.get(filename, 100000000)
                        
                    downloaded_bytes = 0
                    
                    with open(dest_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=1024 * 64):
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            
                            # Calculate overall progress across all files
                            current_overall = overall_downloaded + downloaded_bytes
                            pct = (current_overall / total_est_bytes) * 100.0
                            pct = min(pct, 99.0)  # Keep at 99% until verified
                            
                            mb_downloaded = downloaded_bytes / (1024 * 1024)
                            mb_total = total_bytes / (1024 * 1024)
                            
                            msg = f"Downloading {filename}: {mb_downloaded:.1f} MB / {mb_total:.1f} MB"
                            
                            if progress_callback:
                                progress_callback(msg, pct)
                                
                overall_downloaded += est_sizes.get(filename, downloaded_bytes)
                logger.info(f"Finished downloading {filename}.")
                
        if progress_callback:
            progress_callback("Download completed! Initializing models...", 100.0)

    def load_model(self):
        """Loads the Kokoro model into memory if not already loaded."""
        if self.kokoro is not None:
            return

        if not self.is_model_downloaded():
            raise FileNotFoundError("TTS model files are not downloaded. Please download them first.")

        logger.info("Initializing Kokoro ONNX engine...")
        try:
            self.kokoro = Kokoro(
                model_path=str(self.model_path),
                voices_path=str(self.voices_path)
            )
            logger.info("Kokoro ONNX engine initialized successfully.")
        except Exception as e:
            logger.exception("Failed to load Kokoro ONNX model")
            raise RuntimeError(f"Failed to initialize TTS model: {str(e)}")

    def _split_text_into_chunks(self, text: str, max_chars: int = 400) -> list[str]:
        """
        Splits text into chunks of maximum max_chars characters.
        Tries to split at sentence boundaries, then word boundaries.
        """
        # Clean whitespaces
        text = " ".join(text.split())
        
        # Simple sentence splitter
        sentence_endings = re.compile(r'(?<=[.!?])\s+')
        sentences = sentence_endings.split(text)
        
        chunks = []
        current_chunk = []
        current_len = 0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # If a single sentence is larger than max_chars, split it by words
            if len(sentence) > max_chars:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                
                words = sentence.split(" ")
                sub_chunk = []
                sub_len = 0
                for word in words:
                    if sub_len + len(word) + 1 > max_chars:
                        chunks.append(" ".join(sub_chunk))
                        sub_chunk = [word]
                        sub_len = len(word)
                    else:
                        sub_chunk.append(word)
                        sub_len += len(word) + 1
                if sub_chunk:
                    chunks.append(" ".join(sub_chunk))
                continue

            if current_len + len(sentence) + 1 > max_chars:
                chunks.append(" ".join(current_chunk))
                current_chunk = [sentence]
                current_len = len(sentence)
            else:
                current_chunk.append(sentence)
                current_len += len(sentence) + 1
                
        if current_chunk:
            chunks.append(" ".join(current_chunk))
            
        return chunks

    def _generate_chunk_audio(self, chunk: str, voice: str, speed: float) -> np.ndarray:
        """
        Generate audio for a single text chunk using Kokoro.
        Handles both sync and async versions of the Kokoro API.
        """
        try:
            # Try the standard create() call without the invalid 'order' parameter
            result = self.kokoro.create(
                text=chunk,
                voice=voice,
                speed=speed
            )
            
            # kokoro.create() returns (samples, sample_rate)
            if isinstance(result, tuple) and len(result) == 2:
                samples, sr = result
                return samples
            else:
                raise ValueError(f"Unexpected return type from kokoro.create(): {type(result)}")
                
        except TypeError as e:
            # If create() is actually async, we need to handle that
            error_str = str(e)
            logger.warning(f"kokoro.create() raised TypeError: {error_str}")
            
            # Try running it in an event loop if it's a coroutine
            if "coroutine" in error_str or "await" in error_str:
                logger.info("Detected async kokoro API — running via asyncio")
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        self.kokoro.create(text=chunk, voice=voice, speed=speed)
                    )
                    if isinstance(result, tuple) and len(result) == 2:
                        samples, sr = result
                        return samples
                    raise ValueError(f"Unexpected return from async kokoro.create(): {type(result)}")
                finally:
                    loop.close()
            else:
                raise

    def generate_audio(
        self,
        text: str,
        output_path: Path,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        volume: float = 1.0,
        progress_callback=None
    ) -> float:
        """
        Generates audio for the given text and saves it as a WAV file.
        Returns the duration of the generated audio in seconds.
        """
        self.load_model()
        
        # Split text into manageable chunks for Kokoro
        chunks = self._split_text_into_chunks(text)
        total_chunks = len(chunks)
        logger.info(f"Generating TTS in {total_chunks} chunks using voice '{voice}'")
        
        audio_arrays = []
        
        for idx, chunk in enumerate(chunks):
            if progress_callback:
                percent = (idx / total_chunks) * 100
                progress_callback(f"Generating audio chunk {idx+1}/{total_chunks}", percent)
            
            try:
                samples = self._generate_chunk_audio(chunk, voice, speed)
                
                if volume != 1.0:
                    samples = samples * volume
                
                audio_arrays.append(samples)
                
            except Exception as e:
                logger.error(f"Error generating chunk {idx+1}/{total_chunks}: {str(e)}")
                logger.error(f"Chunk text (first 100 chars): {chunk[:100]}...")
                
                # Try to continue with remaining chunks rather than failing entirely
                if idx == 0:
                    # If the very first chunk fails, the model is probably broken
                    raise RuntimeError(
                        f"TTS generation failed on the first chunk. "
                        f"This usually means the TTS model is corrupted or incompatible. "
                        f"Try deleting the models/ folder and re-downloading. "
                        f"Error: {str(e)}"
                    )
                else:
                    logger.warning(f"Skipping chunk {idx+1} due to error, continuing with rest...")
                    continue
                
        if not audio_arrays:
            raise ValueError("No audio was generated. All chunks failed.")
            
        # Concatenate all audio arrays
        full_audio = np.concatenate(audio_arrays)
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save as WAV using soundfile
        sf.write(str(output_path), full_audio, AUDIO_SAMPLE_RATE)
        
        # Calculate duration: number of samples / sample rate
        duration = len(full_audio) / AUDIO_SAMPLE_RATE
        logger.info(f"Saved generated audio to {output_path} (Duration: {duration:.2f}s)")
        
        if progress_callback:
            progress_callback("Audio generation complete!", 100.0)
            
        return duration

    def test_voice(self, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> tuple:
        """
        Generates a short test clip to verify TTS is working.
        Returns (samples, sample_rate) tuple.
        """
        self.load_model()
        test_text = "Hello! This is a voice test for the novel condenser audiobook generator."
        
        try:
            samples = self._generate_chunk_audio(test_text, voice, speed)
            logger.info(f"Voice test successful for voice '{voice}': {len(samples)} samples")
            return samples, AUDIO_SAMPLE_RATE
        except Exception as e:
            logger.error(f"Voice test failed for voice '{voice}': {str(e)}")
            raise RuntimeError(f"Voice test failed: {str(e)}")
