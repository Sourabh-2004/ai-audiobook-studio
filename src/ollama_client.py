import asyncio
import json
import logging
import re
import shutil

import httpx

from src.config import OLLAMA_API_URL, DEFAULT_MODEL

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_API_URL):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=600.0)  # 10-minute timeout for large chapters

    async def check_status(self) -> bool:
        """Checks if the local Ollama server is running."""
        try:
            response = await self.client.get(f"{self.base_url}/")
            return response.status_code == 200 and "Ollama is running" in response.text
        except Exception as e:
            logger.warning(f"Ollama server check failed: {str(e)}")
            return False

    async def list_local_models(self) -> list[str]:
        """Lists all downloaded models in the local Ollama instance."""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                return [model["name"] for model in data.get("models", [])]
            return []
        except Exception as e:
            logger.error(f"Failed to fetch local Ollama models: {str(e)}")
            return []

    def _model_is_installed(self, model_name: str, installed_models: list[str]) -> bool:
        target_base = model_name.split(":")[0]
        for installed in installed_models:
            if installed == model_name or installed.split(":")[0] == target_base:
                return True
        return False

    def _calculate_max_tokens(self, word_count: int, user_max_tokens: int = 2048) -> int:
        """
        Dynamically calculates max_tokens to ensure the LLM can produce ~50% of the input.
        
        A typical English word ≈ 1.3 tokens. To get 50% of the words back, we need:
            target_words = word_count * 0.50
            target_tokens = target_words * 1.3
            
        We add 25% headroom so the LLM doesn't get cut off mid-sentence.
        Minimum is always 2048 to handle short chapters.
        """
        target_words = int(word_count * 0.55)  # 55% to give slight headroom over 50%
        target_tokens = int(target_words * 1.4)  # ~1.4 tokens per word with safety margin
        
        # Use the larger of: dynamic calculation, user setting, or minimum 2048
        calculated = max(2048, target_tokens, user_max_tokens)
        
        # Cap at 32768 to stay within most model context windows
        return min(calculated, 32768)

    async def condense_chapter(
        self,
        chapter_text: str,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.5,
        max_tokens: int = 2048
    ) -> str:
        """
        Sends a single chapter to Ollama to be condensed to ~50% of original length.
        Dynamically adjusts max_tokens based on input length to prevent truncation.
        """
        # Calculate actual word count and dynamic token limit
        word_count = len(chapter_text.split())
        target_word_count = int(word_count * 0.50)
        dynamic_max_tokens = self._calculate_max_tokens(word_count, max_tokens)
        
        logger.info(
            f"Chapter stats: {word_count} words input → target ~{target_word_count} words output "
            f"(max_tokens: {dynamic_max_tokens})"
        )
        
        system_prompt = (
            "You are a professional literary editor specializing in novel condensation.\n\n"
            "YOUR CRITICAL MISSION:\n"
            f"Rewrite this chapter to EXACTLY 50% of its original length.\n"
            f"The original chapter is {word_count} words long.\n"
            f"Your output MUST be approximately {target_word_count} words.\n\n"
            "This is NOT a summary. This is a CONDENSED REWRITE that reads like a real novel.\n\n"
            "ABSOLUTE RULES — PRESERVE:\n"
            "• Every important plot event and story beat\n"
            "• Exact chronological order of events\n"
            "• All character names exactly as written\n"
            "• Important dialogue (condense but keep key lines verbatim)\n"
            "• Emotional moments, tension, and atmosphere\n"
            "• Story continuity and chapter-to-chapter flow\n"
            "• The narrative voice and writing style\n\n"
            "WHAT TO CUT:\n"
            "• Repetitive descriptions and restated information\n"
            "• Excessive scenery and environmental padding\n"
            "• Unnecessary internal monologue and overthinking\n"
            "• Filler narration that doesn't advance the plot\n"
            "• Redundant dialogue tags and attribution\n"
            "• Overlong action sequences (condense, don't remove)\n\n"
            "WHAT YOU MUST NEVER DO:\n"
            "• Do NOT invent new events or add anything not in the original\n"
            "• Do NOT change the ending or outcome of any scene\n"
            "• Do NOT rename or alter characters\n"
            "• Do NOT change the tense or point of view\n"
            "• Do NOT reorder events\n"
            "• Do NOT write a summary or bullet points — write prose\n"
            "• Do NOT add editorial commentary or meta-text\n"
            "• Do NOT start with phrases like 'Here is the condensed version'\n\n"
            "OUTPUT FORMAT:\n"
            "• Write ONLY the condensed chapter text\n"
            "• Start directly with the story content\n"
            "• Maintain natural paragraph breaks\n"
            "• The result should read like a professionally edited, tighter version of the novel\n\n"
            f"REMEMBER: Your output MUST be approximately {target_word_count} words "
            f"(50% of the original {word_count} words). "
            "If your output is significantly shorter than this, you are over-compressing. "
            "Write MORE content to hit the 50% target."
        )

        user_message = (
            f"Condense the following chapter to approximately {target_word_count} words "
            f"(50% of {word_count} words). Write the condensed version directly — "
            f"no preamble, no commentary, just the rewritten chapter:\n\n"
            f"{chapter_text}"
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "options": {
                "temperature": temperature,
                "num_predict": dynamic_max_tokens
            },
            "stream": False
        }

        try:
            logger.info(f"Sending chapter condensation request to Ollama using model '{model}' "
                        f"(dynamic max_tokens={dynamic_max_tokens})")
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"Ollama returned error status {response.status_code}: {response.text}")
                raise RuntimeError(f"Ollama API error: {response.text}")
                
            result = response.json()
            message_content = result.get("message", {}).get("content", "")
            if not message_content:
                raise ValueError("Ollama returned an empty response.")
            
            # Clean up any meta-text the LLM might prepend
            cleaned = message_content.strip()
            
            # Remove common LLM preambles if present
            preamble_patterns = [
                r"^(?:Here(?:\s+is|'s)\s+the\s+condensed\s+(?:version|chapter)[:\s]*\n*)",
                r"^(?:Condensed\s+(?:version|chapter)[:\s]*\n*)",
                r"^(?:\*\*Condensed\s+Chapter\*\*[:\s]*\n*)",
            ]
            for pattern in preamble_patterns:
                cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
            
            output_word_count = len(cleaned.split())
            ratio = (output_word_count / word_count * 100) if word_count > 0 else 0
            logger.info(
                f"Condensation result: {output_word_count} words "
                f"({ratio:.1f}% of original {word_count} words)"
            )
            
            return cleaned.strip()
            
        except httpx.ConnectError:
            logger.exception("Could not connect to Ollama. Make sure it is installed and running.")
            raise ConnectionError("Ollama is not running. Please launch the Ollama application locally.")
        except Exception as e:
            logger.exception(f"Exception during chapter condensation: {str(e)}")
            raise e

    def _sanitize_status_message(self, message: str) -> str:
        """Strip terminal control/spinner chars that break Windows consoles."""
        cleaned = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", message)
        return cleaned.encode("ascii", errors="ignore").decode("ascii").strip()

    async def _pull_model_via_cli(self, model_name: str, progress_callback=None) -> bool:
        """Pull a model using the local Ollama CLI (most reliable on Windows)."""
        ollama_bin = shutil.which("ollama")
        if not ollama_bin:
            raise RuntimeError(
                "Ollama CLI not found in PATH. Install Ollama from https://ollama.com "
                "and ensure it is running."
            )

        logger.info(f"Pulling model '{model_name}' via Ollama CLI")
        proc = await asyncio.create_subprocess_exec(
            ollama_bin,
            "pull",
            model_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        if proc.stdout is None:
            raise RuntimeError("Failed to capture Ollama CLI output.")

        while True:
            line = await proc.stdout.readline()
            if not line:
                break

            msg = self._sanitize_status_message(
                line.decode("utf-8", errors="replace").strip()
            )
            if not msg:
                continue

            pct = 0.0
            if "%" in msg:
                try:
                    pct = float(msg.rsplit("%", 1)[0].split()[-1])
                except ValueError:
                    pass

            if progress_callback:
                try:
                    progress_callback(msg, pct)
                except Exception:
                    logger.debug("Progress callback failed while pulling model", exc_info=True)

        return_code = await proc.wait()
        if return_code != 0:
            raise RuntimeError(f"Ollama CLI pull failed with exit code {return_code}")

        return True

    async def _pull_model_via_api(self, model_name: str, progress_callback=None) -> bool:
        """Pull a model via Ollama's HTTP API with byte-stream parsing."""
        logger.info(f"Pulling model '{model_name}' via Ollama HTTP API")

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json={"name": model_name},
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(f"Ollama pull failed with HTTP {response.status_code}: {body}")

                buffer = ""
                async for chunk in response.aiter_bytes():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        status = data.get("status", "")
                        completed = data.get("completed", 0)
                        total = data.get("total", 0)

                        if total > 0:
                            pct = (completed / total) * 100.0
                            msg = (
                                f"{status}: {completed / (1024 * 1024):.1f} MB / "
                                f"{total / (1024 * 1024):.1f} MB"
                            )
                        else:
                            msg = status
                            pct = 0.0

                        if progress_callback:
                            try:
                                progress_callback(msg, pct)
                            except Exception:
                                logger.debug("Progress callback failed while pulling model", exc_info=True)

        return True

    async def pull_model(self, model_name: str, progress_callback=None) -> bool:
        """
        Pulls a model from the Ollama registry.
        Prefers the local Ollama CLI on Windows, with HTTP API fallback.
        """
        try:
            if not await self.check_status():
                raise ConnectionError(
                    "Ollama is offline. Please launch the Ollama application locally."
                )

            if shutil.which("ollama"):
                success = await self._pull_model_via_cli(model_name, progress_callback)
            else:
                success = await self._pull_model_via_api(model_name, progress_callback)

            installed = await self.list_local_models()
            if not self._model_is_installed(model_name, installed):
                raise RuntimeError(
                    f"Model '{model_name}' was not found after pull completed. "
                    "Check the model name in project settings."
                )

            logger.info(f"Ollama successfully pulled model '{model_name}'")
            return success
        except Exception as e:
            logger.error(f"Failed to pull model '{model_name}' from Ollama: {str(e)}")
            raise e

    async def close(self):
        await self.client.aclose()
