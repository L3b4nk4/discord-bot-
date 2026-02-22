"""
AI Service - Handles all AI-related operations.
Supports Gemini, OpenRouter (FREE), and Groq.
"""
import asyncio
import os
import aiohttp

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
            "You are Manga, a Discord bot assistant. "
            "Be accurate, concise, and action-oriented."
        )

        # Optional provider selector (auto|gemini|openrouter|groq)
        self.preferred_provider = os.getenv("AI_PROVIDER", "auto").strip().lower()

        # Gemini config
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        self.gemini_base = "https://generativelanguage.googleapis.com/v1beta"
        
        # OpenRouter config
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", self.FREE_MODELS[0])
        self.openrouter_base = "https://openrouter.ai/api/v1"
        
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

    def _init_gemini_or_fallback(self, groq_key: str):
        if self.gemini_key:
            self.enabled = True
            self.provider = "gemini"
            print("✅ AI Service: Gemini initialized")
            print(f"   Model: {self.gemini_model}")
            return
        self._init_openrouter_or_fallback(groq_key)

    def _init_openrouter_or_fallback(self, groq_key: str):
        if self.openrouter_key:
            self.enabled = True
            self.provider = "openrouter"
            print("✅ AI Service: OpenRouter initialized (FREE)")
            print(f"   Model: {self.openrouter_model}")
            return
        self._init_groq_or_fallback(groq_key)

    def _init_groq_or_fallback(self, groq_key: str):
        if groq_key and GROQ_AVAILABLE:
            try:
                self.groq_client = Groq(api_key=groq_key)
                self.enabled = True
                self.provider = "groq"
                print("✅ AI Service: Groq initialized")
                return
            except Exception as e:
                print(f"⚠️ AI Service: Failed to init Groq - {e}")

        print("❌ AI Service: No API keys found.")
        print("   Set GEMINI_API_KEY or OPENROUTER_API_KEY or GROQ_API_KEY")
    
    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def generate_gemini(self, prompt: str, model: str = None) -> str:
        """Generate text using Google Gemini API."""
        if not self.gemini_key:
            return "Gemini is not configured."

        model = model or self.gemini_model
        url = f"{self.gemini_base}/models/{model}:generateContent?key={self.gemini_key}"

        payload = {
            "systemInstruction": {
                "parts": [{"text": self.system_prompt}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 1024,
            },
        }

        try:
            session = await self._get_session()
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    err = data.get("error", {}).get("message") if isinstance(data, dict) else None
                    return f"AI Error: {err or f'Gemini HTTP {resp.status}'}"

                candidates = data.get("candidates", []) if isinstance(data, dict) else []
                if not candidates:
                    return "AI Error: empty Gemini response."

                parts = candidates[0].get("content", {}).get("parts", [])
                text_chunks = []
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        text_chunks.append(part["text"])

                text = "".join(text_chunks).strip()
                return text or "AI Error: Gemini returned no text."
        except asyncio.TimeoutError:
            return "⏱️ Request timed out. Try again."
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
                "temperature": 0.7,
                "max_tokens": 1024,
            }
            
            async with session.post(
                f"{self.openrouter_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
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
            return "⏱️ Request timed out. Try again."
        except Exception as e:
            print(f"❌ OpenRouter Error: {e}")
            return f"AI Error: {str(e)}"

    async def generate_groq(self, prompt: str) -> str:
        """Generate text using Groq (fallback)."""
        if not self.groq_client:
            return "Groq not available."
        try:
            return await asyncio.to_thread(self._run_groq, prompt)
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
            temperature=0.7,
            max_tokens=1024,
        )
        return completion.choices[0].message.content

    async def generate(self, prompt: str) -> str:
        """Generate AI response using active provider."""
        if not self.enabled:
            return "❌ AI not initialized. Set GEMINI_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY."
        
        if self.provider == "gemini":
            return await self.generate_gemini(prompt)
        elif self.provider == "openrouter":
            return await self.generate_openrouter(prompt)
        elif self.provider == "groq":
            return await self.generate_groq(prompt)
        else:
            return "AI provider not found."
    
    async def chat_response(self, username: str, message: str) -> str:
        """Generate a chat response."""
        prompt = f"""You are Manga, a friendly Discord bot assistant.
User '{username}' says: "{message}"
Reply helpfully and concisely (1-2 sentences)."""
        return await self.generate(prompt)
    
    async def voice_response(self, username: str, speech: str) -> str:
        """Generate a voice response."""
        prompt = f"""You are Manga, a voice assistant in a Discord voice chat.
User '{username}' said: "{speech}"
Reply conversationally in 1-2 short sentences. Be friendly and natural."""
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
