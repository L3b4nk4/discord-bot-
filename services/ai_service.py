"""
AI Service - Handles all AI-related operations.
Supports Gemini, OpenRouter (FREE), and Groq.
"""
import asyncio
import os
import aiohttp

# Optional: Import Google GenAI SDK
try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False

# Optional: Import Groq if available for fallback
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class AIService:
    """Handles all AI-related operations using Gemini/OpenRouter/Groq."""
    
    # Free models on OpenRouter (no API credits needed)
    FREE_MODELS = [
        "deepseek/deepseek-chat",                    # Best free model
        "meta-llama/llama-3.1-8b-instruct:free",     # Fast Llama
        "qwen/qwen-2-7b-instruct:free",              # Good Chinese/English
        "mistralai/mistral-7b-instruct:free",        # Fast Mistral
    ]
    
    def __init__(self):
        self.enabled = False
        self.provider = "none"
        self._session = None
        self.system_prompt = (
            "You are Manga, a Discord bot assistant inside a Discord server.\n"
            "Be accurate, concise, practical, and natural.\n"
            "Never invent commands, permissions, moderation actions, or results.\n"
            "If you are unsure, say so briefly instead of guessing.\n"
            "If a request is ambiguous, ask one short clarifying question.\n"
            "Prefer direct answers and short actionable wording."
        )
        self.temperature = min(
            1.2, max(0.0, float(os.getenv("AI_TEMPERATURE", "0.45"))))
        self.max_tokens = max(256, int(os.getenv("AI_MAX_TOKENS", "900")))

        # Optional provider selector (auto|gemini|openrouter|groq)
        self.preferred_provider = os.getenv("AI_PROVIDER", "auto").strip().lower()

        # Gemini config
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        self.gemini_client = None
        if self.gemini_key and GENAI_AVAILABLE:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_key)
            except Exception as e:
                print(f"⚠️ AI Service: Failed to init Gemini SDK client - {e}")
        elif self.gemini_key and not GENAI_AVAILABLE:
            print("⚠️ AI Service: Gemini key found, but `google-genai` is not installed.")
        
        # OpenRouter config
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", self.FREE_MODELS[0])
        self.openrouter_base = "https://openrouter.ai/api/v1"
        self.provider_timeout = max(5, int(os.getenv("AI_PROVIDER_TIMEOUT", "12")))
        self.total_timeout = max(self.provider_timeout, int(os.getenv("AI_TOTAL_TIMEOUT", "20")))
        
        # Groq fallback config
        self.groq_client = None
        groq_key = os.getenv("GROQ_API_KEY")

        # Provider init order.
        if self.preferred_provider in {"gemini", "google"}:
            self._init_gemini_or_fallback(groq_key)
        elif self.preferred_provider in {"openrouter", "or"}:
            self._init_openrouter_or_fallback(groq_key)
        elif self.preferred_provider in {"groq"}:
            self._init_groq_or_fallback(groq_key)
        else:
            # Auto mode: Gemini -> OpenRouter -> Groq
            self._init_gemini_or_fallback(groq_key)

    def _init_gemini(self) -> bool:
        if self.gemini_key and self.gemini_client:
            self.enabled = True
            self.provider = "gemini"
            print("✅ AI Service: Gemini initialized")
            print(f"   Model: {self.gemini_model}")
            return True
        return False

    def _init_openrouter(self) -> bool:
        if self.openrouter_key:
            self.enabled = True
            self.provider = "openrouter"
            print("✅ AI Service: OpenRouter initialized (FREE)")
            print(f"   Model: {self.openrouter_model}")
            return True
        return False

    def _init_groq(self, groq_key: str) -> bool:
        if groq_key and GROQ_AVAILABLE:
            try:
                self.groq_client = Groq(api_key=groq_key)
                self.enabled = True
                self.provider = "groq"
                print("✅ AI Service: Groq initialized")
                return True
            except Exception as e:
                print(f"⚠️ AI Service: Failed to init Groq - {e}")
        return False

    def _init_gemini_or_fallback(self, groq_key: str):
        if self._init_gemini():
            return
        # Requested fallback: Gemini -> OpenRouter -> Groq.
        if self._init_openrouter():
            return
        self._init_groq(groq_key)

    def _init_openrouter_or_fallback(self, groq_key: str):
        if self._init_openrouter():
            return
        self._init_groq_or_fallback(groq_key)

    def _init_groq_or_fallback(self, groq_key: str):
        if self._init_groq(groq_key):
            return
        self._init_openrouter()

        if self.enabled:
            return
        print("❌ AI Service: No API keys found.")
        print("   Set GEMINI_API_KEY and/or GROQ_API_KEY and/or OPENROUTER_API_KEY")

    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    def _run_gemini(self, prompt: str, model: str) -> str:
        if not self.gemini_client:
            raise RuntimeError("Gemini SDK client is not initialized.")

        full_prompt = (
            f"{self.system_prompt}\n\n"
            f"User request:\n{prompt}"
        )
        response = self.gemini_client.models.generate_content(
            model=model,
            contents=full_prompt,
        )

        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        candidates = getattr(response, "candidates", []) or []
        chunks = []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", []) if content else []
            for part in parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    chunks.append(part_text.strip())

        merged = " ".join(chunks).strip()
        if merged:
            return merged

        raise RuntimeError("Gemini returned no text.")

    @staticmethod
    def _is_error_response(text: str) -> bool:
        if not isinstance(text, str):
            return True
        value = text.strip()
        if not value:
            return True
        prefixes = (
            "AI Error:",
            "Thinking Error:",
            "⏱️",
            "Groq not available.",
            "OpenRouter not configured.",
            "Gemini is not configured.",
            "❌",
        )
        return value.startswith(prefixes)

    async def generate_gemini(self, prompt: str, model: str = None) -> str:
        """Generate text using Google GenAI SDK."""
        if not self.gemini_key:
            return "Gemini is not configured."
        if not self.gemini_client:
            return "AI Error: Gemini SDK client unavailable."

        model = model or self.gemini_model
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_gemini, prompt, model),
                timeout=self.provider_timeout,
            )
        except asyncio.TimeoutError:
            return f"⏱️ Request timed out after {self.provider_timeout}s. Try again."
        except Exception as e:
            print(f"❌ Gemini Error: {e}")
            return f"AI Error: {str(e)}"
    
    async def generate_openrouter(self, prompt: str, model: str = None) -> str:
        """Generate text using OpenRouter (OpenAI-compatible API)."""
        if not self.openrouter_key:
            return "OpenRouter not configured."
        
        model = model or self.openrouter_model
        
        try:
            session = await self._get_session()
            headers = {
                "Authorization": f"Bearer {self.openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/your-project",  # Optional
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            
            async with session.post(
                f"{self.openrouter_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.provider_timeout)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    error = await resp.text()
                    print(f"❌ OpenRouter Error ({resp.status}): {error}")
                    # Try fallback model if primary fails
                    if model == self.openrouter_model and len(self.FREE_MODELS) > 1:
                        fallback = self.FREE_MODELS[1]
                        print(f"🔄 Trying fallback model: {fallback}")
                        return await self.generate_openrouter(prompt, fallback)
                    return f"AI Error: {error[:100]}"
                    
        except asyncio.TimeoutError:
            return f"⏱️ Request timed out after {self.provider_timeout}s. Try again."
        except Exception as e:
            print(f"❌ OpenRouter Error: {e}")
            return f"AI Error: {str(e)}"

    async def generate_groq(self, prompt: str) -> str:
        """Generate text using Groq (fallback)."""
        if not self.groq_client:
            return "Groq not available."
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_groq, prompt),
                timeout=self.provider_timeout,
            )
        except asyncio.TimeoutError:
            return f"⏱️ Request timed out after {self.provider_timeout}s. Try again."
        except Exception as e:
            print(f"❌ Groq Error: {e}")
            return f"Thinking Error: {str(e)}"

    def _run_groq(self, prompt):
        completion = self.groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return completion.choices[0].message.content

    @staticmethod
    def _clean_user_text(text: str) -> str:
        return (text or "").strip()

    def _format_history(self, history) -> str:
        if not history:
            return ""

        lines = []
        for item in history[-10:]:
            if not isinstance(item, dict):
                continue

            role = str(item.get("role", "user")).strip().lower()
            content = self._clean_user_text(item.get("content", ""))
            if not content:
                continue

            name = self._clean_user_text(item.get("name", ""))
            if role == "assistant":
                speaker = "Manga"
            else:
                speaker = name or "User"

            lines.append(f"{speaker}: {content}")

        return "\n".join(lines)

    def _build_chat_prompt(self, username: str, message: str, history=None) -> str:
        parts = [
            "Mode: Discord text chat.",
            "Rules:",
            "- Reply in 1-3 short sentences unless a short list is clearly better.",
            "- Be helpful and natural, not robotic.",
            "- Do not pretend you already changed server state.",
            "- If the user asks for bot help and you do not know the exact command, say so briefly.",
        ]

        history_block = self._format_history(history)
        if history_block:
            parts.append("Conversation context so far (oldest to newest):")
            parts.append(history_block)
            parts.append("Continue the same conversation naturally. Do not restart from the beginning.")

        parts.append(f"User: {username}")
        parts.append(f"Message: {self._clean_user_text(message)}")
        return "\n".join(parts)

    def _build_voice_prompt(self, username: str, speech: str) -> str:
        return (
            "Mode: Discord voice chat.\n"
            "Rules:\n"
            "- Reply conversationally in 1-2 short sentences.\n"
            "- Keep phrasing easy to speak aloud.\n"
            "- Do not invent actions or claim you already did something unless it truly happened.\n"
            f"User: {username}\n"
            f"Speech: {self._clean_user_text(speech)}"
        )

    async def _generate_with_fallback(self, prompt: str) -> str:
        """Generate AI response using active provider and fallback order."""
        if not self.enabled:
            return "❌ AI not initialized. Set GEMINI_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY."
        
        if self.provider == "gemini":
            gemini_result = await self.generate_gemini(prompt)
            if not self._is_error_response(gemini_result):
                return gemini_result

            # Requested fallback path: Gemini -> OpenRouter -> Groq.
            if self.openrouter_key:
                print("🔄 Gemini failed, falling back to OpenRouter.")
                openrouter_result = await self.generate_openrouter(prompt)
                if not self._is_error_response(openrouter_result):
                    return openrouter_result

            if self.groq_client:
                print("🔄 Gemini/OpenRouter failed, falling back to Groq.")
                groq_result = await self.generate_groq(prompt)
                if not self._is_error_response(groq_result):
                    return groq_result

            return gemini_result
        elif self.provider == "openrouter":
            return await self.generate_openrouter(prompt)
        elif self.provider == "groq":
            return await self.generate_groq(prompt)
        else:
            return "AI provider not found."

    async def generate(self, prompt: str) -> str:
        """Generate AI response with an overall timeout guard."""
        try:
            return await asyncio.wait_for(
                self._generate_with_fallback(prompt),
                timeout=self.total_timeout,
            )
        except asyncio.TimeoutError:
            print(f"⚠️ AI Service total timeout reached after {self.total_timeout}s")
            return "⏱️ I'm taking too long right now. Please try again."
    
    async def chat_response(self, username: str, message: str, history=None) -> str:
        """Generate a chat response."""
        prompt = self._build_chat_prompt(username, message, history=history)
        return await self.generate(prompt)
    
    async def voice_response(self, username: str, speech: str) -> str:
        """Generate a voice response."""
        prompt = self._build_voice_prompt(username, speech)
        return await self.generate(prompt)
    
    async def list_free_models(self) -> str:
        """List available free models."""
        lines = ["**🆓 Free OpenRouter Models:**\n"]
        for m in self.FREE_MODELS:
            marker = "→ " if m == self.openrouter_model else "  "
            lines.append(f"{marker}`{m}`")
        return "\n".join(lines)

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
