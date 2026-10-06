import os
import wave
import subprocess
import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def is_ffmpeg_available() -> bool:
    """Checks if FFmpeg is available in the system PATH."""
    return shutil.which("ffmpeg") is not None

def get_wav_duration(file_path: Path) -> float:
    """Returns the duration of a WAV file in seconds using the built-in wave module."""
    try:
        with wave.open(str(file_path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            if rate > 0:
                return frames / float(rate)
            return 0.0
    except Exception as e:
        logger.error(f"Error reading WAV duration for {file_path}: {str(e)}")
        return 0.0

def generate_wav_silence(duration: float, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Generates silent PCM bytes of a given duration."""
    num_samples = int(duration * sample_rate)
    # 16-bit PCM is 2 bytes per sample. Zeros represent silence.
    return b"\x00" * (num_samples * channels * sample_width)

def merge_wav_files(input_paths: list[Path], output_path: Path, pause_duration: float = 2.0):
    """
    Merges multiple WAV files into a single WAV file.
    Inserts a silent pause of pause_duration (seconds) between files.
    """
    if not input_paths:
        raise ValueError("No input files provided for merging.")

    logger.info(f"Merging {len(input_paths)} WAV files into {output_path} with {pause_duration}s pause.")
    
    # Read parameters from the first file
    try:
        with wave.open(str(input_paths[0]), "rb") as first_wav:
            params = first_wav.getparams()
            sample_rate = first_wav.getframerate()
            channels = first_wav.getnchannels()
            sample_width = first_wav.getsampwidth()
    except Exception as e:
        logger.error(f"Failed to read WAV header from {input_paths[0]}: {str(e)}")
        raise e

    # Generate silence buffer
    silence_data = generate_wav_silence(
        pause_duration,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width
    )

    try:
        with wave.open(str(output_path), "wb") as output_wav:
            output_wav.setparams(params)
            
            for idx, path in enumerate(input_paths):
                if not path.exists():
                    logger.warning(f"Audio file not found during merge: {path}. Skipping.")
                    continue
                
                with wave.open(str(path), "rb") as input_wav:
                    # Check if audio settings match
                    if (input_wav.getframerate() != sample_rate or
                        input_wav.getnchannels() != channels or
                        input_wav.getsampwidth() != sample_width):
                        logger.warning(f"Format mismatch for {path}, skipping from merge.")
                        continue
                    
                    # Read and write PCM data
                    data = input_wav.readframes(input_wav.getnframes())
                    output_wav.writeframes(data)
                
                # If not the last file, write silence
                if idx < len(input_paths) - 1 and pause_duration > 0:
                    output_wav.writeframes(silence_data)
                    
        logger.info(f"Successfully merged WAV files into {output_path}")
    except Exception as e:
        logger.error(f"Error merging WAV files: {str(e)}")
        if output_path.exists():
            output_path.unlink()
        raise e

def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> bool:
    """
    Converts a WAV file to MP3 using FFmpeg if available.
    Returns True if successful, False otherwise.
    """
    if not is_ffmpeg_available():
        logger.warning("FFmpeg is not installed. Cannot convert WAV to MP3.")
        return False
        
    logger.info(f"Converting {wav_path} to {mp3_path} using FFmpeg.")
    try:
        # Run FFmpeg command in background
        cmd = [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame",
            "-qscale:a", "2",
            str(mp3_path)
        ]
        # Run command, capture stdout/stderr to avoid terminal clutter
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"FFmpeg conversion completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg command failed with code {e.returncode}. Stderr: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Failed to run FFmpeg conversion: {str(e)}")
        return False
