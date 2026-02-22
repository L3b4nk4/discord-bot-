"""
LLM Agent Service - Provides LLM capabilities using OpenRouter (FREE).
No local setup required - uses OpenRouter's free API models.
"""
import asyncio
import aiohttp
import os
from services.http_session_mixin import HTTPSessionMixin
from services.openrouter_config import OPENROUTER_BASE


class LLMAgentService(HTTPSessionMixin):
    """Handles LLM operations using OpenRouter (free cloud API)."""
    
    # Free models on OpenRouter
    FREE_MODELS = [
        "deepseek/deepseek-chat",
        "meta-llama/llama-3.1-8b-instruct:free",
        "qwen/qwen-2-7b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
    ]
    
    def __init__(self, default_model: str = None):
        self.enabled = False
        self.init_error = None
        self._session = None
        
        # OpenRouter config
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        self.default_model = default_model or os.getenv('OPENROUTER_MODEL', self.FREE_MODELS[0])
        self.base_url = OPENROUTER_BASE
        
        # Check if we have API key
        if self.api_key:
            self.enabled = True
            print(f"✅ LLM Agent: OpenRouter initialized (FREE)")
            print(f"   Model: {self.default_model}")
        else:
            self.init_error = "No OPENROUTER_API_KEY set. Get free key at openrouter.ai"
            print(f"⚠️ LLM Agent: {self.init_error}")
        
    async def ensure_ready(self):
        """Ensure the service is ready."""
        return self.enabled
    
    async def prompt(self, message: str, model: str = None) -> str:
        """Send a prompt to OpenRouter and get a response."""
        if not self.enabled:
            return f"❌ LLM Agent unavailable: {self.init_error}"
        
        model = model or self.default_model
        
        try:
            session = await self._get_session()
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": message}],
                "temperature": 0.7,
                "max_tokens": 1024,
            }
            
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    error_text = await resp.text()
                    print(f"❌ OpenRouter Error ({resp.status}): {error_text}")
                    # Try fallback model
                    if model == self.default_model and len(self.FREE_MODELS) > 1:
                        fallback = self.FREE_MODELS[1]
                        print(f"🔄 Trying fallback: {fallback}")
                        return await self.prompt(message, fallback)
                    return f"❌ Error: {error_text[:100]}"
                    
        except asyncio.TimeoutError:
            return "⏱️ Request timed out. Try again."
        except aiohttp.ClientError as e:
            return f"❌ Connection error: {e}"
        except Exception as e:
            print(f"❌ LLM Agent Error: {e}")
            return f"❌ An error occurred: {str(e)}"
    
    async def chat(self, message: str, conversation_id: str = None, model: str = None) -> str:
        """Chat with the model."""
        return await self.prompt(message, model)
    
    async def list_models(self) -> str:
        """List available free models."""
        lines = ["**🆓 Free OpenRouter Models:**\n"]
        for m in self.FREE_MODELS:
            marker = "→ " if m == self.default_model else "  "
            lines.append(f"{marker}`{m}`")
        lines.append(f"\n💡 Set model: `OPENROUTER_MODEL=model-name`")
        return "\n".join(lines)
    
    async def pull_model(self, model_name: str) -> str:
        """Not needed for OpenRouter - models are cloud-based."""
        return f"✅ No pull needed! OpenRouter models are cloud-based.\n💡 Just use: `!agent model:{model_name} your prompt`"
    
    async def agent_task(self, task: str, model: str = None) -> str:
        """Execute an agent task."""
        prompt = f"""You are an AI agent assistant. Execute this task:

Task: {task}

Provide a clear, actionable response. If the task requires multiple steps, list them clearly."""
        
        return await self.prompt(prompt, model)
    
