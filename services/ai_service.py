"""
AI Service - Handles all AI-related operations.
Supports OpenRouter (FREE) and Groq as fallback.
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
    """Handles all AI-related operations using OpenRouter (free) or Groq."""
    
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
        
        # OpenRouter config
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", self.FREE_MODELS[0])
        self.openrouter_base = "https://openrouter.ai/api/v1"
        
        # Groq fallback config
        self.groq_client = None
        groq_key = os.getenv("GROQ_API_KEY")
        
        # 1. Try OpenRouter first (FREE!)
        if self.openrouter_key:
            self.enabled = True
            self.provider = "openrouter"
            print(f"✅ AI Service: OpenRouter initialized (FREE)")
            print(f"   Model: {self.openrouter_model}")
        # 2. Fallback to Groq
        elif groq_key and GROQ_AVAILABLE:
            try:
                self.groq_client = Groq(api_key=groq_key)
                self.enabled = True
                self.provider = "groq"
                print("✅ AI Service: Groq initialized (Fallback)")
            except Exception as e:
                print(f"⚠️ AI Service: Failed to init Groq - {e}")
        else:
            print("❌ AI Service: No API keys found. Set OPENROUTER_API_KEY (free!)")
            print("   Get yours at: https://openrouter.ai (no credit card needed)")
    
    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
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
                "messages": [{"role": "user", "content": prompt}],
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
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        return completion.choices[0].message.content

    async def generate(self, prompt: str) -> str:
        """Generate AI response using active provider."""
        if not self.enabled:
            return "❌ AI not initialized. Set OPENROUTER_API_KEY (free at openrouter.ai)"
        
        if self.provider == "openrouter":
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
